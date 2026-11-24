import json
from pathlib import Path
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth, database, tts_routes
from backend.config import settings
from backend.tts_delivery import TtsDelivery
from backend.tts_library import TtsLibrary
from backend.tts_protocol import (
    CHUNK,
    END,
    PLAYBACK_DONE,
    PLAYBACK_STARTED,
    READY,
    START,
    STREAMING_AUDIO_BYTES,
    pack_control,
)


class FakeEdgeTts:
    voice = "zh-CN-XiaoxiaoNeural"
    sample_rate = 16_000

    def __init__(self):
        self.texts = []

    async def synthesize_wav(self, text: str, output_path: Path):
        self.texts.append(text)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(b"\x23\x00" * 1600)

    async def stream_pcm(self, text: str, *, output_rate=None):
        self.texts.append(text)
        yield b"\x23\x00" * 1600

    async def stream_mp3(self, text: str):
        self.texts.append(text)
        yield b"edge-mp3-part"

    async def convert_mp3_to_wav(self, source: Path, output_path: Path):
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(b"\x23\x00" * 1600)


def test_reply_text_to_synthesis_pcm_downlink_and_hat_playback_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "tts-e2e.sqlite3"))
    database.init_db()
    auth.register_user("ttse2e", "ttse2e@example.com", "TtsE2e123")
    token = auth.create_session("ttse2e", "TtsE2e123", False)["token"]
    headers = {"Authorization": "Bearer " + token}
    library = TtsLibrary(tmp_path / "tts-library")
    fake_edge = FakeEdgeTts()
    delivery = TtsDelivery(library, synthesizer=fake_edge)
    monkeypatch.setattr(tts_routes, "tts_delivery", delivery)
    app = FastAPI()
    app.include_router(tts_routes.router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tts?device_id=hat-e2e") as hat:
            assert hat.receive_json()["type"] == "tts_connected"
            hat.send_bytes(pack_control(READY, 0))
            response = client.post("/api/tts/speak", json={
                "device_id": "hat-e2e", "text": "识字完成，前方写着出口。",
            }, headers=headers)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["status"] == "synthesizing"
            main_tts_text = "识字完成，前方写着出口。"

            start_message = hat.receive()
            assert "bytes" in start_message, start_message
            start = start_message["bytes"]
            assert start[3] == START
            request_id = int.from_bytes(start[4:8], "little")
            assert int.from_bytes(start[8:12], "little") == 16_000
            assert int.from_bytes(start[12:16], "little") == STREAMING_AUDIO_BYTES
            hat.send_bytes(pack_control(PLAYBACK_STARTED, request_id))

            pcm = bytearray()
            sequence = 0
            while True:
                frame_message = hat.receive()
                assert "bytes" in frame_message, frame_message
                frame = frame_message["bytes"]
                if frame[3] == CHUNK:
                    assert int.from_bytes(frame[8:12], "little") == sequence
                    pcm.extend(frame[12:])
                    sequence += 1
                elif frame[3] == END:
                    assert int.from_bytes(frame[8:12], "little") == sequence
                    break
                else:
                    raise AssertionError(f"unexpected TTS frame type: {frame[3]}")

            assert fake_edge.texts == [main_tts_text]

            assert pcm == b"\x23\x00" * 1600
            hat.send_bytes(pack_control(PLAYBACK_DONE, request_id, sequence))
            client.portal.call(delivery.peers["hat-e2e"].queue.join)

    cached = library.get_by_text(main_tts_text)
    assert cached is not None
    assert library.load_pcm(cached) == b"\x23\x00" * 1600
    assert json.loads(library.manifest_path.read_text(encoding="utf-8"))["phrases"][0]["phrase_id"] == cached.phrase_id


def test_tts_route_explains_device_sample_rate_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "tts-rate.sqlite3"))
    database.init_db()
    auth.register_user("ttsrates", "ttsrates@example.com", "TtsRates123")
    token = auth.create_session("ttsrates", "TtsRates123", False)["token"]
    headers = {"Authorization": "Bearer " + token}

    library = TtsLibrary(tmp_path / "tts-library")
    source = tmp_path / "wrong-rate.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(b"\0\0" * 2400)
    library.register_generated_wav(
        "wrong_rate", "这条语音采样率不兼容。", "zh-CN-XiaoxiaoNeural",
        source, sample_rate=24_000,
    )
    delivery = TtsDelivery(library, synthesizer=FakeEdgeTts())
    monkeypatch.setattr(tts_routes, "tts_delivery", delivery)
    app = FastAPI()
    app.include_router(tts_routes.router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tts?device_id=hat-rate") as hat:
            assert hat.receive_json()["type"] == "tts_connected"
            hat.send_bytes(pack_control(READY, 0))
            response = client.post("/api/tts/speak", json={
                "device_id": "hat-rate", "text": "这条语音采样率不兼容。",
            }, headers=headers)
            assert response.status_code == 422
            assert "16 kHz" in response.json()["detail"]


def test_browser_edge_tts_route_reuses_the_pcm_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "tts-browser.sqlite3"))
    database.init_db()
    auth.register_user("ttsweb", "ttsweb@example.com", "TtsWeb123")
    token = auth.create_session("ttsweb", "TtsWeb123", False)["token"]
    headers = {"Authorization": "Bearer " + token}
    fake_edge = FakeEdgeTts()
    delivery = TtsDelivery(TtsLibrary(tmp_path / "tts-library"), synthesizer=fake_edge)
    monkeypatch.setattr(tts_routes, "tts_delivery", delivery)
    app = FastAPI()
    app.include_router(tts_routes.router)
    with TestClient(app) as client:
        payload = {"text": "电脑端也使用统一 Edge TTS。"}
        first = client.post("/api/tts/synthesize-browser", json=payload, headers=headers)
        second = client.post("/api/tts/synthesize-browser", json=payload, headers=headers)
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("audio/wav")
        assert second.status_code == 200
        assert first.content == second.content
        assert fake_edge.texts == [payload["text"]]


def test_browser_stream_ticket_streams_mp3_and_warms_pcm_for_next_play(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "tts-browser-stream.sqlite3"))
    database.init_db()
    auth.register_user("ttsstream", "ttsstream@example.com", "TtsStream123")
    token = auth.create_session("ttsstream", "TtsStream123", False)["token"]
    headers = {"Authorization": "Bearer " + token}
    fake_edge = FakeEdgeTts()
    delivery = TtsDelivery(TtsLibrary(tmp_path / "tts-library"), synthesizer=fake_edge)
    monkeypatch.setattr(tts_routes, "tts_delivery", delivery)
    app = FastAPI()
    app.include_router(tts_routes.router)
    with TestClient(app) as client:
        payload = {"text": "首包流式测试，正在合成。"}
        ticket_response = client.post("/api/tts/browser-ticket", json=payload, headers=headers)
        assert ticket_response.status_code == 200
        assert ticket_response.json()["cached"] is False
        first_url = ticket_response.json()["url"]
        first = client.get(first_url)
        assert first.status_code == 200
        assert first.headers["content-type"] == "audio/mpeg"
        assert first.content == b"edge-mp3-part"
        assert delivery.library.get_by_text(payload["text"]) is not None

        second_ticket = client.post("/api/tts/browser-ticket", json=payload, headers=headers)
        assert second_ticket.json()["cached"] is True
        second = client.get(second_ticket.json()["url"])
        assert second.headers["content-type"].startswith("audio/wav")
        assert fake_edge.texts == [payload["text"]]
