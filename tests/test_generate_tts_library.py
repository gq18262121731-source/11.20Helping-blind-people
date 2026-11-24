import asyncio
import json
import wave

import pytest

from backend.tts_library import TtsLibrary
from tools import generate_tts_library


def test_generator_creates_versioned_local_wav_manifest(tmp_path, monkeypatch):
    config_path = tmp_path / "phrases.json"
    config_path.write_text(json.dumps({
        "voice": "zh-CN-XiaoxiaoNeural",
        "sample_rate": 16_000,
        "phrases": [{"phrase_id": "front_person", "text": "前方有行人，请注意。"}],
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_synthesize(text, voice, mp3_path):
        mp3_path.write_bytes(b"prepared-audio")

    def fake_convert(mp3_path, wav_path, sample_rate, ffmpeg):
        with wave.open(str(wav_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"\x01\x00" * 1600)

    monkeypatch.setattr(generate_tts_library, "_synthesize", fake_synthesize)
    monkeypatch.setattr(generate_tts_library, "_convert", fake_convert)

    result = asyncio.run(generate_tts_library.generate(config_path, tmp_path / "library", "ffmpeg"))
    library = TtsLibrary(tmp_path / "library")
    phrase = library.get_by_id("front_person")

    assert result["phrase_count"] == 1
    assert phrase.duration_ms == 100
    assert len(library.load_pcm(phrase)) == 3200
    manifest = json.loads((tmp_path / "library" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["format"] == "pcm_s16le"
    assert manifest["phrases"][0]["text"] == "前方有行人，请注意。"


def test_generator_rejects_an_empty_phrase_list(tmp_path):
    config_path = tmp_path / "phrases.json"
    config_path.write_text(json.dumps({"phrases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="非空列表"):
        generate_tts_library._read_phrases(config_path)
