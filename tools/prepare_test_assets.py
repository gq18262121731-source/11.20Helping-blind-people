"""Prepare small, reproducible real-model acceptance assets.

Large models and generated test variants live under ``test_assets`` and are
ignored by git.  Network failures are recorded in manifest.json as BLOCKED;
the script never manufactures a PASS result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "test_assets"
IMAGE_URL = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/resources/test_files/ch_en_num.jpg"
ASR_ARCHIVE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
ASR_ARCHIVE_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, retries: int = 3) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size:
        return target
    last = None
    for attempt in range(1, retries + 1):
        part = target.with_suffix(target.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Morning-Star-acceptance/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if part.stat().st_size == 0:
                raise IOError("empty download")
            part.replace(target)
            return target
        except Exception as error:
            last = error
            if part.exists():
                part.unlink()
            if attempt < retries:
                time.sleep(attempt * 2)
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def image_variants(source: Path):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(str(source), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"cannot decode image: {source}")
    images = {
        "rotated.jpg": cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
        "blurred.jpg": cv2.GaussianBlur(image, (9, 9), 0),
        "blank.jpg": 255 * (image * 0 + 1).astype("uint8"),
    }
    for name, value in images.items():
        target = ASSETS / "images" / name
        ok, encoded = cv2.imencode(".jpg", value, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError(f"cannot encode image variant: {name}")
        target.write_bytes(encoded.tobytes())


def prepare(download_asr: bool = True):
    previous = {}
    manifest_path = ASSETS / "manifest.json"
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "assets": dict(previous.get("assets", {})), "blocked": [],
        "license": {"image": "RapidOCR repository test resource; verify upstream terms before redistribution",
                     "audio_model": "sherpa-onnx release assets; see upstream LICENSE",
                     "audio": "Official sherpa-onnx bundled test_wavs/zh.wav"},
    }
    image_target = ASSETS / "images" / "ch_en_num.jpg"
    try:
        download(IMAGE_URL, image_target)
        image_variants(image_target)
        for path in sorted((ASSETS / "images").glob("*.jpg")):
            manifest["assets"][f"images/{path.name}"] = {"sha256": sha256(path), "bytes": path.stat().st_size,
                "source": IMAGE_URL if path.name == "ch_en_num.jpg" else "generated from ch_en_num.jpg"}
    except Exception as error:
        manifest["blocked"].append({"asset": "ocr_images", "reason": str(error)})
    if download_asr:
        archive = ASSETS / "models" / ASR_ARCHIVE_NAME
        try:
            download(ASR_ARCHIVE_URL, archive)
            extract_root = ASSETS / "models"
            marker = extract_root / ".extracted"
            if not marker.exists():
                with tarfile.open(archive, "r:bz2") as tar:
                    tar.extractall(extract_root)
                marker.write_text("ok", encoding="utf-8")
            model_root = extract_root / ASR_ARCHIVE_NAME.removesuffix(".tar.bz2")
            wav = model_root / "test_wavs" / "zh.wav"
            manifest["assets"]["audio/zh.wav"] = {"sha256": sha256(wav), "bytes": wav.stat().st_size,
                "source": ASR_ARCHIVE_URL, "expected_text": None,
                "expected_text_note": "Official sample has no transcript file in the release archive; CER is not scored."}
            for name in ("model.int8.onnx", "tokens.txt"):
                path = model_root / name
                manifest["assets"][f"models/{name}"] = {"sha256": sha256(path), "bytes": path.stat().st_size,
                    "source": ASR_ARCHIVE_URL}
        except Exception as error:
            manifest["blocked"].append({"asset": "sherpa_onnx_sense_voice", "reason": str(error)})
    elif previous.get("blocked"):
        manifest["blocked"].extend(previous["blocked"])
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (ASSETS / "audio").mkdir(exist_ok=True)
    (ASSETS / "audio" / "metadata.json").write_text(json.dumps({"source": ASR_ARCHIVE_URL,
        "expected_text": manifest["assets"].get("audio/zh.wav", {}).get("expected_text"),
        "note": "Official archive does not ship a transcript; observed text is reported separately."}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if manifest["blocked"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-asr-model", action="store_true")
    args = parser.parse_args()
    sys.exit(prepare(download_asr=not args.no_asr_model))
