"""Validated local WAV assets used for low-latency device speech playback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import wave
from pathlib import Path
from typing import Iterable


PHRASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_PHRASE_SECONDS = 30
MAX_WAV_BYTES = 8 * 1024 * 1024


class TtsLibraryError(ValueError):
    """A missing or invalid local TTS asset or manifest."""


def _synchronized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return guarded


@dataclass(frozen=True)
class TtsPhrase:
    phrase_id: str
    text: str
    path: Path
    voice: str
    sample_rate: int
    duration_ms: int
    byte_length: int
    sha256: str


def validate_phrase_id(value: object) -> str:
    phrase_id = str(value or "")
    if not PHRASE_ID.fullmatch(phrase_id):
        raise TtsLibraryError("phrase_id must contain 1-64 lowercase letters, digits, dots, underscores or hyphens")
    return phrase_id


class TtsLibrary:
    """Loads a manifest and streams verified PCM WAV data from local storage."""

    def __init__(self, root: str | Path):
        self._lock = threading.RLock()
        self.root = Path(root).expanduser().resolve()
        self.manifest_path = self.root / "manifest.json"
        self._manifest_signature: tuple[int, int] | None = None
        self._by_id: dict[str, dict] = {}
        self._by_text: dict[str, str] = {}
        self._sample_rate = 0
        self._voice = ""

    @property
    def available(self) -> bool:
        self._reload_if_changed()
        return bool(self._by_id)

    @_synchronized
    def summary(self) -> dict:
        self._reload_if_changed()
        return {
            "available": bool(self._by_id),
            "phrase_count": len(self._by_id),
            "sample_rate": self._sample_rate or None,
            "phrase_ids": sorted(self._by_id),
        }

    @_synchronized
    def get_by_id(self, phrase_id: str) -> TtsPhrase:
        self._reload_if_changed()
        phrase_id = validate_phrase_id(phrase_id)
        row = self._by_id.get(phrase_id)
        if row is None:
            raise TtsLibraryError("phrase_id is not present in the local voice library")
        return self._phrase_metadata(phrase_id, row)

    @_synchronized
    def get_by_text(self, text: str) -> TtsPhrase | None:
        self._reload_if_changed()
        phrase_id = self._by_text.get(str(text).strip())
        return self.get_by_id(phrase_id) if phrase_id else None

    @_synchronized
    def register_generated_wav(
        self, phrase_id: str, text: str, voice: str, wav_path: str | Path,
        *, sample_rate: int,
    ) -> TtsPhrase:
        """Atomically add a generated WAV to the local cache manifest."""
        phrase_id = validate_phrase_id(phrase_id)
        text = str(text or "").strip()
        voice = str(voice or "").strip()
        if not text or len(text) > 500 or not voice:
            raise TtsLibraryError("generated phrase text or voice is invalid")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
            raise TtsLibraryError("generated phrase sample rate is invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        self._reload_if_changed()
        cached = self.get_by_text(text)
        if cached is not None:
            return cached
        if self._by_id and self._sample_rate != sample_rate:
            raise TtsLibraryError("generated audio sample rate does not match the local library")

        source = Path(wav_path)
        try:
            with wave.open(str(source), "rb") as audio:
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                        audio.getframerate() != sample_rate or audio.getcomptype() != "NONE"):
                    raise TtsLibraryError("generated WAV must be mono pcm_s16le at the library sample rate")
                frames = audio.getnframes()
                duration_ms = frames * 1000 // sample_rate
            if frames <= 0 or duration_ms > MAX_PHRASE_SECONDS * 1000:
                raise TtsLibraryError("generated WAV duration is outside the supported range")
            if source.stat().st_size > MAX_WAV_BYTES:
                raise TtsLibraryError("generated WAV exceeds the local asset size limit")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except (OSError, wave.Error) as error:
            raise TtsLibraryError("generated WAV is unreadable") from error

        revision = f"dynamic-{phrase_id}"
        relative = Path("revisions") / revision / f"{phrase_id}.wav"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged_asset = destination.with_name(destination.name + ".tmp")
        shutil.copyfile(source, staged_asset)
        os.replace(staged_asset, destination)

        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            manifest = {
                "version": 1,
                "voice": voice,
                "audio": {"format": "pcm_s16le", "sample_rate": sample_rate, "channels": 1, "bits_per_sample": 16},
                "phrases": [],
            }
        except (OSError, json.JSONDecodeError) as error:
            raise TtsLibraryError("cannot update an invalid TTS manifest") from error
        audio_format = manifest.get("audio", {})
        if (manifest.get("version") != 1 or audio_format.get("format") != "pcm_s16le" or
                audio_format.get("channels") != 1 or audio_format.get("sample_rate") != sample_rate or
                not isinstance(manifest.get("phrases"), list)):
            raise TtsLibraryError("local TTS manifest is incompatible with generated audio")
        phrases = [row for row in manifest["phrases"] if row.get("phrase_id") != phrase_id]
        if any(row.get("text", "").strip() == text for row in phrases):
            return self.get_by_text(text)  # Another request registered the same text.
        phrases.append({
            "phrase_id": phrase_id,
            "text": text,
            "voice": voice,
            "path": relative.as_posix(),
            "duration_ms": duration_ms,
            "byte_length": frames * 2,
            "sha256": digest,
            "cache_generated": True,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        manifest["phrases"] = phrases
        manifest["voice"] = manifest.get("voice") or voice
        manifest["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fd, temporary_manifest = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
                json.dump(manifest, target, ensure_ascii=False, indent=2)
                target.write("\n")
            os.replace(temporary_manifest, self.manifest_path)
        finally:
            if os.path.exists(temporary_manifest):
                os.unlink(temporary_manifest)
        self._manifest_signature = None
        self._reload_if_changed()
        return self.get_by_id(phrase_id)

    @_synchronized
    def prune_generated_cache(
        self,
        *,
        pinned_paths: Iterable[str | Path] = (),
        max_entries: int = 512,
        max_bytes: int = 128 * 1024 * 1024,
    ) -> int:
        """Prune oldest generated WAV assets while keeping active/queued paths."""
        self._reload_if_changed()
        pinned = {Path(path).resolve() for path in pinned_paths}
        generated = []
        total_bytes = 0
        for phrase_id, row in self._by_id.items():
            if not row.get("cache_generated"):
                continue
            path = Path(row["path"])
            try:
                stat = path.stat()
                file_bytes = stat.st_size
                modified = stat.st_mtime_ns
            except OSError:
                file_bytes = 0
                modified = 0
            total_bytes += file_bytes
            generated.append((modified, phrase_id, path, file_bytes))
        excess_count = max(0, len(generated) - max_entries)
        excess_bytes = max(0, total_bytes - max_bytes)
        if not excess_count and not excess_bytes:
            return 0

        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TtsLibraryError("cannot prune an unreadable TTS manifest") from error
        remove_ids = set()
        for _modified, phrase_id, path, file_bytes in sorted(generated):
            if path in pinned:
                continue
            remove_ids.add(phrase_id)
            excess_count = max(0, excess_count - 1)
            excess_bytes = max(0, excess_bytes - file_bytes)
            if not excess_count and not excess_bytes:
                break
        if not remove_ids:
            return 0
        manifest["phrases"] = [row for row in manifest["phrases"] if row.get("phrase_id") not in remove_ids]
        fd, temporary_manifest = tempfile.mkstemp(prefix=".manifest-prune-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
                json.dump(manifest, target, ensure_ascii=False, indent=2)
                target.write("\n")
            os.replace(temporary_manifest, self.manifest_path)
        finally:
            if os.path.exists(temporary_manifest):
                os.unlink(temporary_manifest)
        for _modified, phrase_id, path, _file_bytes in generated:
            if phrase_id in remove_ids:
                path.unlink(missing_ok=True)
        self._manifest_signature = None
        self._reload_if_changed()
        return len(remove_ids)

    @_synchronized
    def load_pcm(self, phrase: TtsPhrase) -> bytes:
        """Read and verify an asset; callers should run this off the event loop."""
        self._validate_asset(phrase)
        try:
            with wave.open(str(phrase.path), "rb") as audio:
                pcm = audio.readframes(audio.getnframes())
        except (OSError, wave.Error) as error:
            raise TtsLibraryError("could not read local WAV asset") from error
        if len(pcm) != phrase.byte_length or len(pcm) % 2:
            raise TtsLibraryError("PCM data length does not match the manifest")
        return pcm

    def _reload_if_changed(self) -> None:
        try:
            stat = self.manifest_path.stat()
        except FileNotFoundError:
            self._manifest_signature = None
            self._by_id = {}
            self._by_text = {}
            self._sample_rate = 0
            self._voice = ""
            return
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._manifest_signature:
            return
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TtsLibraryError("local TTS manifest is unreadable") from error
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise TtsLibraryError("unsupported local TTS manifest version")
        audio = manifest.get("audio")
        if not isinstance(audio, dict) or audio.get("format") != "pcm_s16le" or audio.get("channels") != 1:
            raise TtsLibraryError("local TTS assets must be mono pcm_s16le")
        sample_rate = audio.get("sample_rate")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
            raise TtsLibraryError("local TTS sample_rate is invalid")
        rows = manifest.get("phrases")
        if not isinstance(rows, list):
            raise TtsLibraryError("local TTS manifest phrases must be a list")
        by_id: dict[str, dict] = {}
        by_text: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise TtsLibraryError("local TTS phrase entry must be an object")
            phrase_id = validate_phrase_id(row.get("phrase_id"))
            text = row.get("text")
            relative_path = row.get("path")
            digest = row.get("sha256")
            if phrase_id in by_id:
                raise TtsLibraryError("duplicate phrase_id in local TTS manifest")
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                raise TtsLibraryError("local TTS phrase text is empty or too long")
            if not isinstance(relative_path, str) or not relative_path:
                raise TtsLibraryError("local TTS phrase path is invalid")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise TtsLibraryError("local TTS phrase checksum is invalid")
            path = (self.root / relative_path).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise TtsLibraryError("local TTS phrase path escapes the library") from error
            normalized = {**row, "path": str(path), "text": text.strip()}
            by_id[phrase_id] = normalized
            by_text.setdefault(text.strip(), phrase_id)
        self._sample_rate = sample_rate
        self._voice = str(manifest.get("voice") or "")
        self._by_id = by_id
        self._by_text = by_text
        self._manifest_signature = signature

    def _phrase_metadata(self, phrase_id: str, row: dict) -> TtsPhrase:
        path = Path(row["path"])
        duration_ms = row.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 0 < duration_ms <= MAX_PHRASE_SECONDS * 1000:
            raise TtsLibraryError("local TTS phrase duration is invalid")
        byte_length = row.get("byte_length")
        if isinstance(byte_length, bool) or not isinstance(byte_length, int) or byte_length <= 0 or byte_length % 2:
            raise TtsLibraryError("local TTS phrase byte_length is invalid")
        return TtsPhrase(
            phrase_id=phrase_id,
            text=row["text"],
            path=path,
            voice=str(row.get("voice") or self._voice),
            sample_rate=self._sample_rate,
            duration_ms=duration_ms,
            byte_length=byte_length,
            sha256=row["sha256"],
        )

    def _validate_asset(self, phrase: TtsPhrase) -> None:
        path = phrase.path
        try:
            size = path.stat().st_size
        except OSError as error:
            raise TtsLibraryError("local TTS WAV asset is missing") from error
        if not 44 <= size <= MAX_WAV_BYTES:
            raise TtsLibraryError("local TTS WAV asset has an invalid size")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(64 * 1024), b""):
                    digest.update(block)
            with wave.open(str(path), "rb") as audio:
                frame_count = audio.getnframes()
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                        audio.getframerate() != phrase.sample_rate or audio.getcomptype() != "NONE"):
                    raise TtsLibraryError("local TTS WAV format does not match the manifest")
                duration_ms = frame_count * 1000 // audio.getframerate()
        except (OSError, wave.Error) as error:
            raise TtsLibraryError("local TTS WAV asset is invalid") from error
        if duration_ms <= 0 or duration_ms > MAX_PHRASE_SECONDS * 1000:
            raise TtsLibraryError("local TTS WAV duration is outside the supported range")
        actual_digest = digest.hexdigest()
        if actual_digest != phrase.sha256:
            raise TtsLibraryError("local TTS WAV checksum mismatch")
        if duration_ms != phrase.duration_ms or frame_count * 2 != phrase.byte_length:
            raise TtsLibraryError("local TTS WAV metadata does not match the manifest")
