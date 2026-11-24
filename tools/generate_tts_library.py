"""Batch-generate Edge TTS phrases into a versioned local PCM WAV library."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tts_library import MAX_PHRASE_SECONDS, TtsLibraryError, validate_phrase_id


DEFAULT_PHRASES = ROOT / "config" / "tts_phrases.json"
DEFAULT_OUTPUT = ROOT / "backend" / "data" / "tts" / "library"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_SAMPLE_RATE = 16_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_phrases(path: Path) -> tuple[str, int, list[dict[str, str]]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取语音清单：{path}") from error
    if not isinstance(config, dict):
        raise ValueError("语音清单必须是 JSON 对象")
    voice = str(config.get("voice") or DEFAULT_VOICE).strip()
    sample_rate = config.get("sample_rate", DEFAULT_SAMPLE_RATE)
    if not voice or len(voice) > 100:
        raise ValueError("voice 无效")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
        raise ValueError("sample_rate 必须在 8000 到 48000 之间")
    raw_phrases = config.get("phrases")
    if not isinstance(raw_phrases, list) or not raw_phrases:
        raise ValueError("phrases 必须是非空列表；请先整理高频语句及 phrase_id")
    phrases = []
    seen_ids = set()
    seen_texts = set()
    for row in raw_phrases:
        if not isinstance(row, dict):
            raise ValueError("每条语音必须包含 phrase_id 和 text")
        phrase_id = validate_phrase_id(row.get("phrase_id"))
        text = str(row.get("text") or "").strip()
        if phrase_id in seen_ids:
            raise ValueError(f"phrase_id 重复：{phrase_id}")
        if not text or len(text) > 500:
            raise ValueError(f"语音文本为空或超过 500 字：{phrase_id}")
        if text in seen_texts:
            raise ValueError("语音文本重复，请合并成一个 phrase_id")
        seen_ids.add(phrase_id)
        seen_texts.add(text)
        phrases.append({"phrase_id": phrase_id, "text": text})
    return voice, sample_rate, phrases


async def _synthesize(text: str, voice: str, mp3_path: Path) -> None:
    try:
        import edge_tts
    except ImportError as error:
        raise RuntimeError("缺少 Edge TTS 依赖；请安装 backend/requirements-tts.txt") from error
    await edge_tts.Communicate(text=text, voice=voice).save(str(mp3_path))


def _convert(mp3_path: Path, wav_path: Path, sample_rate: int, ffmpeg: str) -> None:
    if shutil.which(ffmpeg) is None and not Path(ffmpeg).is_file():
        raise RuntimeError(f"找不到 FFmpeg：{ffmpeg}。请安装 FFmpeg 并加入 PATH，或使用 --ffmpeg 指定路径。")
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(mp3_path), "-vn", "-map_metadata", "-1",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(wav_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()[-500:]
        raise RuntimeError(f"FFmpeg 转码失败：{detail}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("FFmpeg 转码超时") from error
    try:
        with wave.open(str(wav_path), "rb") as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                    audio.getframerate() != sample_rate or audio.getcomptype() != "NONE"):
                raise RuntimeError("FFmpeg 输出格式与要求的 mono pcm_s16le 不一致")
            duration = audio.getnframes() / sample_rate
    except (OSError, wave.Error) as error:
        raise RuntimeError("FFmpeg 输出不是有效 WAV 文件") from error
    if duration <= 0 or duration > MAX_PHRASE_SECONDS:
        raise RuntimeError("合成语音时长必须大于 0 且不超过 30 秒")


async def generate(phrases_path: Path, output_root: Path, ffmpeg: str) -> dict:
    voice, sample_rate, phrases = _read_phrases(phrases_path)
    output_root.mkdir(parents=True, exist_ok=True)
    revisions = output_root / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=revisions))
    entries = []
    try:
        for phrase in phrases:
            phrase_id = phrase["phrase_id"]
            mp3_path = staging / f"{phrase_id}.mp3"
            wav_path = staging / f"{phrase_id}.wav"
            print(f"合成 {phrase_id}")
            await _synthesize(phrase["text"], voice, mp3_path)
            _convert(mp3_path, wav_path, sample_rate, ffmpeg)
            mp3_path.unlink(missing_ok=True)
            with wave.open(str(wav_path), "rb") as audio:
                frames = audio.getnframes()
            entries.append({
                "phrase_id": phrase_id,
                "text": phrase["text"],
                "voice": voice,
                "file_name": wav_path.name,
                "duration_ms": frames * 1000 // sample_rate,
                "byte_length": frames * 2,
                "sha256": _sha256(wav_path),
            })

        version_input = {
            "voice": voice,
            "sample_rate": sample_rate,
            "format": "pcm_s16le",
            "channels": 1,
            "phrases": entries,
        }
        version = hashlib.sha256(
            json.dumps(version_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        revision_dir = revisions / version
        if revision_dir.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, revision_dir)

        manifest = {
            "version": 1,
            "library_version": version,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "voice": voice,
            "audio": {"format": "pcm_s16le", "sample_rate": sample_rate, "channels": 1, "bits_per_sample": 16},
            "phrases": [
                {
                    "phrase_id": row["phrase_id"],
                    "text": row["text"],
                    "voice": row["voice"],
                    "path": f"revisions/{version}/{row['file_name']}",
                    "duration_ms": row["duration_ms"],
                    "byte_length": row["byte_length"],
                    "sha256": row["sha256"],
                }
                for row in entries
            ],
        }
        manifest_tmp = output_root / f".manifest-{version}.tmp"
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(manifest_tmp, output_root / "manifest.json")
        return {"library_version": version, "phrase_count": len(entries), "output": str(output_root.resolve())}
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a local Edge TTS PCM WAV phrase library")
    parser.add_argument("--phrases", type=Path, default=DEFAULT_PHRASES, help="JSON phrase list")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="local library directory")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable or full path")
    args = parser.parse_args()
    try:
        result = asyncio.run(generate(args.phrases, args.output, args.ffmpeg))
    except (TtsLibraryError, ValueError, RuntimeError) as error:
        print(f"TTS 语音库生成失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
