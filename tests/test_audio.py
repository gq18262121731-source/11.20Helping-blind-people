import asyncio

import pytest

from backend.audio_buffer import AudioBuffer
from backend.audio_protocol import AudioProtocolError, AudioStart, pack_audio_frame, unpack_audio_frame
from backend.intent import Intent, IntentClassifier
from backend.voice_session import VoiceSessionManager, VoiceState
from backend.camera_stream import CameraFrames


def test_audio_header_round_trip_and_validation():
    payload = b"\x01\x00" * 320
    frame = pack_audio_frame(payload, 4, 123)
    assert unpack_audio_frame(frame) == (4, payload)
    with pytest.raises(AudioProtocolError):
        unpack_audio_frame(frame[:-1])


def test_audio_start_preserves_visual_source_and_browser_tts_route():
    start = AudioStart.from_message({
        "type": "audio_start", "device_id": "browser_mic", "session_id": "browser-1",
        "source_id": "local_camera", "source_mode": "software", "input_source": "computer",
        "tts_target": "browser",
    })
    assert start.source_id == "local_camera"
    assert start.source_mode == "software"
    assert start.input_source == "computer"
    assert start.tts_target == "browser"


def test_audio_buffer_is_bounded_ring():
    buffer = AudioBuffer(max_bytes=8)
    buffer.append(b"1234")
    buffer.append(b"567890")
    assert buffer.read() == b"34567890"
    buffer.clear()
    assert len(buffer) == 0


def test_intent_classifier_is_deterministic_without_llm():
    classifier = IntentClassifier()
    assert classifier.classify("帮我读一下前面的文字").intent is Intent.OCR_READ
    assert classifier.classify("再读一遍").intent is Intent.OCR_AGAIN
    assert classifier.classify("停止").intent is Intent.STOP
    assert classifier.classify("前面有什么").intent is Intent.ENV_QUERY
    assert classifier.classify("随便说点什么").intent is Intent.UNKNOWN


class FakeASR:
    async def recognize_pcm(self, pcm):
        return type("Result", (), {"text": "再读一遍"})()


def test_voice_session_transitions_and_single_task():
    async def handler(match, session):
        return {"intent": match.intent.value, "reply": "ok"}

    manager = VoiceSessionManager(recognizer=FakeASR(), intent_handler=handler)
    session = manager.start({"type": "audio_start", "device_id": "simulator_001", "session_id": "s1"})
    manager.append("s1", b"\x01\x00" * 20)
    with pytest.raises(RuntimeError):
        manager.start({"type": "audio_start", "device_id": "simulator_001", "session_id": "s2"})
    result = asyncio.run(manager.finish("s1"))
    assert result["text"] == "再读一遍"
    assert result["intent"] == Intent.OCR_AGAIN.value
    assert manager.state["s1"] is VoiceState.COMPLETED


def test_latest_frame_rejects_stale_sources():
    now = [0.0]
    frames = CameraFrames(max_age=3, clock=lambda: now[0])
    frames.publish("hat_1", 1, b"\xff\xd8x\xff\xd9")
    # The shared helper uses the process-wide cache; this check documents the
    # same age rule used by capture_latest_frame without opening a camera loop.
    assert frames.get("hat_1").seq == 1
    now[0] = 3
    assert frames.get("hat_1") is None
