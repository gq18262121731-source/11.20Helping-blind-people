import hashlib
import json
import wave

import pytest

from backend.tts_library import TtsLibrary, TtsLibraryError


def write_wav(path, *, sample_rate=24_000, frames=960):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames)
    return path.read_bytes()


def write_manifest(root, *, wav_bytes, sample_rate=24_000, checksum=None):
    manifest = {
        "version": 1,
        "library_version": "test-v1",
        "voice": "zh-CN-XiaoxiaoNeural",
        "audio": {"format": "pcm_s16le", "sample_rate": sample_rate, "channels": 1, "bits_per_sample": 16},
        "phrases": [{
            "phrase_id": "front_person",
            "text": "前方有行人，请注意。",
            "voice": "zh-CN-XiaoxiaoNeural",
            "path": "revisions/test-v1/front_person.wav",
            "duration_ms": 40,
            "byte_length": 1920,
            "sha256": checksum or hashlib.sha256(wav_bytes).hexdigest(),
        }],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_local_library_loads_phrase_and_returns_pcm(tmp_path):
    asset = tmp_path / "revisions" / "test-v1" / "front_person.wav"
    asset.parent.mkdir(parents=True)
    wav_bytes = write_wav(asset)
    write_manifest(tmp_path, wav_bytes=wav_bytes)

    library = TtsLibrary(tmp_path)
    phrase = library.get_by_text("前方有行人，请注意。")

    assert phrase is not None
    assert phrase.phrase_id == "front_person"
    assert phrase.sample_rate == 24_000
    assert phrase.duration_ms == 40
    assert library.load_pcm(phrase) == b"\x00\x00" * 960
    assert library.summary()["phrase_count"] == 1


def test_library_reloads_manifest_replaced_by_generator(tmp_path):
    asset = tmp_path / "revisions" / "test-v1" / "front_person.wav"
    asset.parent.mkdir(parents=True)
    wav_bytes = write_wav(asset)
    write_manifest(tmp_path, wav_bytes=wav_bytes)
    library = TtsLibrary(tmp_path)
    assert library.get_by_id("front_person").text == "前方有行人，请注意。"

    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1,
        "voice": "zh-CN-XiaoxiaoNeural",
        "audio": {"format": "pcm_s16le", "sample_rate": 24_000, "channels": 1},
        "phrases": [],
    }), encoding="utf-8")

    assert library.available is False


def test_library_rejects_checksum_mismatch(tmp_path):
    asset = tmp_path / "revisions" / "test-v1" / "front_person.wav"
    asset.parent.mkdir(parents=True)
    wav_bytes = write_wav(asset)
    write_manifest(tmp_path, wav_bytes=wav_bytes, checksum="0" * 64)

    library = TtsLibrary(tmp_path)
    with pytest.raises(TtsLibraryError, match="checksum"):
        library.load_pcm(library.get_by_id("front_person"))


def test_library_rejects_path_escape(tmp_path):
    write_manifest(tmp_path, wav_bytes=b"\x00\x00" * 960)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["phrases"][0]["path"] = "../outside.wav"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TtsLibraryError, match="escapes"):
        TtsLibrary(tmp_path).summary()


def test_generated_cache_prunes_only_unpinned_dynamic_phrases(tmp_path):
    library = TtsLibrary(tmp_path / "library")
    wav_dir = tmp_path / "generated"
    wav_dir.mkdir()
    first_wav = wav_dir / "first.wav"
    second_wav = wav_dir / "second.wav"
    first_bytes = write_wav(first_wav, sample_rate=16_000, frames=320)
    first = library.register_generated_wav("dyn-first", "第一条动态回复", "voice", first_wav, sample_rate=16_000)
    second_bytes = write_wav(second_wav, sample_rate=16_000, frames=320)
    second = library.register_generated_wav("dyn-second", "第二条动态回复", "voice", second_wav, sample_rate=16_000)
    assert first_bytes and second_bytes

    removed = library.prune_generated_cache(max_entries=1, max_bytes=8 * 1024 * 1024,
                                             pinned_paths=[second.path])

    assert removed == 1
    assert library.get_by_text("第二条动态回复") is not None
    assert library.get_by_text("第一条动态回复") is None
    assert not first.path.exists()
