import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import io
import struct
from threading import Barrier
import wave

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import auth, database, family
from backend.asr import ASRError, ASRResult
from backend.camera_stream import camera_frames
from backend.config import settings
from backend.raw_camera import router as camera_router
from backend.services import runtime


DEVICE = "esp32s3_cam_001"


@pytest.fixture
def family_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "family.sqlite3"))
    monkeypatch.setattr(runtime, "devices", {})
    monkeypatch.setattr(runtime, "web_clients", set())
    monkeypatch.setattr(runtime, "locations", {})
    monkeypatch.setattr(runtime, "speech_enabled", True)
    monkeypatch.setattr(runtime, "image_queue", asyncio.Queue(maxsize=1))
    monkeypatch.setattr(runtime, "speech_announced", {})
    monkeypatch.setattr(camera_frames, "_frames", OrderedDict())
    monkeypatch.setattr(camera_frames, "_clock", lambda: 100.0)
    monkeypatch.setattr(family, "_frame_log_times", OrderedDict())
    monkeypatch.setattr(family, "tts_delivery", FakeTtsDelivery())
    database.init_db()
    headers = {}
    for name in ("ownertest", "familytest", "othertest"):
        auth.register_user(name, name + "@example.com", "Family123")
        token = auth.create_session(name, "Family123", False)["token"]
        headers[name] = {"Authorization": "Bearer " + token}
    transport = FastAPI()
    transport.include_router(family.router)
    transport.include_router(camera_router)
    with TestClient(transport) as client:
        response = client.post("/api/family/bindings", headers=headers["ownertest"],
                               json={"device_id": DEVICE, "family_username": "familytest"})
        assert response.status_code == 200
        yield client, headers


def send(client, headers, **overrides):
    return client.post("/api/family/messages", headers=headers,
                       json={"device_id": DEVICE, "body": "前方有人，请慢行", **overrides})


def test_family_endpoints_require_session_and_device_binding(family_client):
    client, headers = family_client
    paths = ["/api/family/bindings", "/api/family/overview?device_id=" + DEVICE,
             "/api/family/messages?device_id=" + DEVICE,
             f"/api/family/cameras/{DEVICE}/frame"]
    for path in paths:
        assert client.get(path).status_code == 401
    for path in paths[1:]:
        assert client.get(path, headers=headers["othertest"]).status_code == 403
    assert send(client, {}).status_code == 401
    assert send(client, headers["othertest"]).status_code == 403
    assert send(client, headers["familytest"], device_id="other_hat").status_code == 403


def test_offline_messages_are_saved_and_never_reported_as_played(family_client):
    client, headers = family_client
    result = send(client, headers["familytest"], message_type="voice_transcript").json()
    assert result["message_type"] == "voice_transcript"
    assert result["delivery"]["status"] == "offline"
    assert "未加入播报队列" in result["delivery"]["detail"]
    saved = client.get("/api/family/messages?device_id=" + DEVICE,
                       headers=headers["ownertest"]).json()
    assert saved[0]["id"] == result["id"]
    assert saved[0]["message_type"] == "voice_transcript"
    assert send(client, headers["familytest"]).json()["message_type"] == "text"


@pytest.mark.parametrize("body", [None, 123, True, {}, [], "", "   ", "字" * 501, "前方😀"])
def test_invalid_text_is_rejected_before_saving_or_speaking(family_client, body):
    client, headers = family_client
    assert send(client, headers["familytest"], body=body).status_code == 422
    with database.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM family_messages").fetchone()[0] == 0


@pytest.mark.parametrize("kind", [None, "audio", "voice", {}, [], True])
def test_invalid_message_type_is_rejected(family_client, kind):
    client, headers = family_client
    assert send(client, headers["familytest"], message_type=kind).status_code == 422


class SpeechDevice:
    protocol = 2

    def __init__(self, accept=True):
        self.accept = accept
        self.messages = []

    def queue_family_message(self, text):
        self.messages.append(text)
        return self.accept


class FakeTtsDelivery:
    class Library:
        available = True

    def __init__(self):
        self.peers = {}
        self.library = self.Library()
        self.messages = []
        self.status = "synthesizing"

    def queue_text(self, device_id, text, *, priority=1):
        if device_id not in self.peers:
            return {"status": "device_offline"}
        self.messages.append((device_id, text, priority))
        return {"status": self.status}


def test_message_is_queued_once_and_content_is_never_globally_broadcast(family_client):
    client, headers = family_client
    device = SpeechDevice()
    runtime.devices[DEVICE] = device
    family.tts_delivery.peers[DEVICE] = object()

    class UnrelatedViewer:
        async def send_json(self, message):
            raise AssertionError("private family content reached an unrelated viewer")

    runtime.web_clients.add(UnrelatedViewer())
    result = send(client, headers["familytest"], message_type="voice_transcript").json()
    assert result["delivery"]["status"] == "synthesizing"
    assert "正在合成语音" in result["delivery"]["detail"]
    assert family.tts_delivery.messages == [(DEVICE, "前方有人，请慢行", 1)]
    assert len(runtime.web_clients) == 1
    speech = client.get("/api/family/overview?device_id=" + DEVICE,
                        headers=headers["familytest"]).json()["speech"]
    assert speech["available"] is True
    assert speech["status"] == "ready"
    assert speech["mode"] == "tts"


@pytest.mark.parametrize("case,expected", [
    ("offline", "offline"), ("disabled", "speech_disabled"), ("full", "queue_full"),
])
def test_delivery_status_reflects_real_connection_state(family_client, case, expected):
    client, headers = family_client
    if case != "offline":
        family.tts_delivery.peers[DEVICE] = object()
    if case == "full":
        family.tts_delivery.status = "queue_full"
    if case == "disabled":
        runtime.speech_enabled = False
    result = send(client, headers["familytest"]).json()
    assert result["delivery"]["status"] == expected
    if case != "full":
        assert not family.tts_delivery.messages
    if case != "full":
        overview = client.get("/api/family/overview?device_id=" + DEVICE,
                              headers=headers["familytest"]).json()
        assert overview["speech"]["available"] is False
        assert overview["speech"]["status"] == expected


def test_revoked_message_and_camera_permissions_take_effect_immediately(family_client):
    client, headers = family_client
    runtime.devices[DEVICE] = SpeechDevice()
    camera_frames.publish(DEVICE, 1, b"\xff\xd8frame\xff\xd9")
    with database.db() as connection:
        connection.execute("UPDATE device_family_bindings SET can_view_camera=0, can_send_messages=0")
    assert send(client, headers["familytest"]).status_code == 403
    assert client.get(f"/api/family/cameras/{DEVICE}/frame",
                      headers=headers["familytest"]).status_code == 403
    overview = client.get("/api/family/overview?device_id=" + DEVICE,
                          headers=headers["familytest"]).json()
    assert overview["speech"]["available"] is False
    assert overview["speech"]["status"] == "not_authorized"
    assert overview["camera"]["online"] is False
    assert not family.tts_delivery.messages


def test_camera_polling_logs_view_intervals_without_logging_every_frame(family_client, monkeypatch):
    client, headers = family_client
    clock = [10.0]
    monkeypatch.setattr(family, "monotonic", lambda: clock[0])
    camera_frames.publish(DEVICE, 7, b"\xff\xd8frame\xff\xd9")
    url = f"/api/family/cameras/{DEVICE}/frame"
    for _ in range(8):
        response = client.get(url, headers=headers["familytest"])
        assert response.status_code == 200
        assert response.headers["x-frame-seq"] == "7"
    clock[0] += 31
    assert client.get(url, headers=headers["familytest"]).status_code == 200
    assert client.get(url, headers=headers["ownertest"]).status_code == 200
    with database.db() as connection:
        rows = connection.execute("SELECT actor_username FROM family_access_logs WHERE action='view_camera_frame'").fetchall()
    assert [row[0] for row in rows] == ["familytest", "familytest", "ownertest"]


def test_revoking_binding_immediately_blocks_camera_and_message_history(family_client):
    client, headers = family_client
    binding = client.get("/api/family/bindings", headers=headers["ownertest"]).json()[0]
    assert client.delete(f"/api/family/bindings/{binding['id']}",
                         headers=headers["ownertest"]).status_code == 200
    assert client.get("/api/family/messages?device_id=" + DEVICE,
                      headers=headers["familytest"]).status_code == 403
    assert send(client, headers["familytest"]).status_code == 403


def test_family_message_is_sent_only_over_the_tts_channel(family_client):
    client, headers = family_client
    family.tts_delivery.peers[DEVICE] = object()
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        # Invalid-frame response is a barrier proving socket registration finished.
        ws.send_bytes(b"invalid")
        assert ws.receive_text().startswith("ERR:BAD_FRAME:")
        result = send(client, headers["familytest"], body="前方路口请向右走").json()
        assert result["delivery"]["status"] == "synthesizing"
        assert family.tts_delivery.messages == [(DEVICE, "前方路口请向右走", 1)]


def test_single_account_can_register_view_and_announce_to_its_own_hat(family_client):
    client, headers = family_client
    with database.db() as connection:
        connection.execute("DELETE FROM device_family_bindings")
        connection.execute("DELETE FROM auth_sessions WHERE username != 'ownertest'")
        connection.execute("DELETE FROM auth_users WHERE username != 'ownertest'")
        assert connection.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0] == 1
    response = client.post("/api/family/devices", headers=headers["ownertest"],
                           json={"device_id": DEVICE})
    assert response.status_code == 200
    registered = response.json()
    assert registered["created"] is True
    assert registered["is_owner_device"] is True
    assert registered["owner_username"] == registered["family_username"] == "ownertest"

    bindings = client.get("/api/family/bindings", headers=headers["ownertest"]).json()
    assert len(bindings) == 1
    assert bindings[0]["id"] == registered["id"]
    assert bindings[0]["is_owner_device"] is True
    runtime.devices[DEVICE] = SpeechDevice()
    family.tts_delivery.peers[DEVICE] = object()
    camera_frames.publish(DEVICE, 1, b"\xff\xd8owner-frame\xff\xd9")
    overview = client.get("/api/family/overview?device_id=" + DEVICE,
                          headers=headers["ownertest"]).json()
    assert overview["role"] == "owner"
    assert overview["permissions"] == {"camera": True, "location": True, "messages": True}
    assert overview["speech"]["available"] is True
    frame = client.get(f"/api/family/cameras/{DEVICE}/frame", headers=headers["ownertest"])
    assert frame.content == b"\xff\xd8owner-frame\xff\xd9"
    sent = send(client, headers["ownertest"], message_type="voice_transcript").json()
    assert sent["recipient_username"] == "ownertest"
    assert sent["delivery"]["status"] == "synthesizing"
    assert family.tts_delivery.messages == [(DEVICE, "前方有人，请慢行", 1)]


def test_owner_device_registration_is_idempotent(family_client):
    client, headers = family_client
    body = {"device_id": "owner_hat_002"}
    first = client.post("/api/family/devices", headers=headers["ownertest"], json=body)
    repeated = client.post("/api/family/devices", headers=headers["ownertest"], json=body)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["created"] is True
    assert repeated.json()["created"] is False
    assert first.json()["id"] == repeated.json()["id"]
    with database.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM device_family_bindings WHERE device_id=?",
                                  (body["device_id"],)).fetchone()[0] == 1


def test_device_claim_requires_session_and_rejects_other_owners(family_client):
    client, headers = family_client
    assert client.post("/api/family/devices", json={"device_id": DEVICE}).status_code == 401
    for account in ("familytest", "othertest"):
        assert client.post("/api/family/devices", headers=headers[account],
                           json={"device_id": DEVICE}).status_code == 403
    assert client.post("/api/family/devices", headers=headers["ownertest"],
                       json={"device_id": "local_camera"}).status_code == 422
    assert client.post("/api/family/devices", headers=headers["ownertest"],
                       json={}).status_code == 422


def test_registering_owner_record_preserves_existing_family_permissions(family_client):
    client, headers = family_client
    with database.db() as connection:
        connection.execute("UPDATE device_family_bindings SET can_view_camera=0, can_send_messages=0")
    assert client.post("/api/family/devices", headers=headers["ownertest"],
                       json={"device_id": DEVICE}).status_code == 200
    runtime.devices[DEVICE] = SpeechDevice()
    family.tts_delivery.peers[DEVICE] = object()
    owner = client.get("/api/family/overview?device_id=" + DEVICE,
                       headers=headers["ownertest"]).json()
    assert owner["permissions"]["camera"] is True
    assert owner["permissions"]["messages"] is True
    assert owner["speech"]["available"] is True
    family_overview = client.get("/api/family/overview?device_id=" + DEVICE,
                                 headers=headers["familytest"]).json()
    assert family_overview["permissions"]["camera"] is False
    assert family_overview["permissions"]["messages"] is False
    assert send(client, headers["familytest"]).status_code == 403
    assert send(client, headers["ownertest"]).json()["delivery"]["status"] == "synthesizing"
    bindings = client.get("/api/family/bindings", headers=headers["ownertest"]).json()
    assert [binding["is_owner_device"] for binding in bindings] == [True, False]


def test_family_invitation_still_requires_a_distinct_account(family_client):
    client, headers = family_client
    response = client.post("/api/family/bindings", headers=headers["ownertest"],
                           json={"device_id": "new_hat", "family_username": "OWNERTEST"})
    assert response.status_code == 422


def test_owner_registration_and_family_invitation_cannot_race_to_claim_device(family_client):
    _client, headers = family_client
    start = Barrier(2)
    device_id = "concurrent_claim_hat"

    def register_owner():
        start.wait(timeout=5)
        try:
            asyncio.run(family.register_owner_device(
                {"device_id": device_id}, headers["ownertest"]["Authorization"]))
            return "ownertest", 200
        except HTTPException as error:
            return "ownertest", error.status_code

    def invite_family():
        start.wait(timeout=5)
        try:
            asyncio.run(family.create_family_binding(
                {"device_id": device_id, "family_username": "familytest"},
                headers["othertest"]["Authorization"]))
            return "othertest", 200
        except HTTPException as error:
            return "othertest", error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_task = executor.submit(register_owner)
        invitation_task = executor.submit(invite_family)
        outcomes = dict([owner_task.result(timeout=10), invitation_task.result(timeout=10)])
    assert sorted(outcomes.values()) == [200, 403]
    winner = next(account for account, status in outcomes.items() if status == 200)
    with database.db() as connection:
        owners = connection.execute(
            "SELECT DISTINCT owner_username FROM device_family_bindings WHERE device_id=?",
            (device_id,),
        ).fetchall()
    assert [row[0] for row in owners] == [winner]


class VoiceRecognizer:
    available = True

    def __init__(self):
        self.calls = []
        self.text = "前方路口请慢行"
        self.error = None
        self.started = None
        self.release = None

    async def recognize_wav(self, data):
        self.calls.append(data)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return ASRResult(text=self.text)


@pytest.fixture
def voice_client(family_client, monkeypatch):
    recognizer = VoiceRecognizer()
    monkeypatch.setattr(family, "asr", recognizer)
    monkeypatch.setattr(family, "_voice_lock", asyncio.Lock())
    client, headers = family_client
    yield client, headers, recognizer


def wav_recording(*, frames=8_000, sample_rate=16_000, channels=1, width=2, silence=False):
    stream = io.BytesIO()
    sample = b"\0" * width if silence or width != 2 else struct.pack("<h", 1500)
    with wave.open(stream, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(sample_rate)
        recording.writeframes(sample * frames * channels)
    return stream.getvalue()


def transcribe(client, authorization, *, content=None, content_type="audio/wav", device_id=DEVICE):
    return client.post("/api/family/voice/transcribe?device_id=" + device_id,
                       content=wav_recording() if content is None else content,
                       headers={**authorization, "Content-Type": content_type})


def test_voice_transcription_requires_session_device_binding_and_message_permission(voice_client):
    client, headers, recognizer = voice_client
    assert transcribe(client, {}).status_code == 401
    assert transcribe(client, headers["othertest"]).status_code == 403
    assert transcribe(client, headers["familytest"], device_id="other_hat").status_code == 403
    with database.db() as connection:
        connection.execute("UPDATE device_family_bindings SET can_send_messages=0")
    assert transcribe(client, headers["familytest"]).status_code == 403
    assert recognizer.calls == []


def test_transcription_returns_confirmation_text_without_saving_or_speaking(voice_client):
    client, headers, recognizer = voice_client
    runtime.devices[DEVICE] = SpeechDevice()
    data = wav_recording()
    response = transcribe(client, headers["familytest"], content=data)
    assert response.status_code == 200
    assert response.json() == {"text": "前方路口请慢行", "mode": "transcript", "requires_confirmation": True}
    assert recognizer.calls == [data]
    assert not family.tts_delivery.messages
    with database.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM family_messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM family_access_logs").fetchone()[0] == 0
    assert not family._voice_lock.locked()


@pytest.mark.parametrize("data", [
    b"", b"not a wav", wav_recording(frames=0), wav_recording(frames=100),
    wav_recording(sample_rate=44_100), wav_recording(channels=2),
    wav_recording(width=1), wav_recording()[:-2], wav_recording(silence=True),
])
def test_voice_upload_rejects_invalid_short_truncated_or_silent_wav_before_asr(voice_client, data):
    client, headers, recognizer = voice_client
    assert transcribe(client, headers["familytest"], content=data).status_code == 400
    assert recognizer.calls == []
    assert not family._voice_lock.locked()


def test_voice_upload_requires_wav_content_type(voice_client):
    client, headers, recognizer = voice_client
    assert transcribe(client, headers["familytest"], content_type="image/jpeg").status_code == 415
    assert recognizer.calls == []


def test_voice_upload_bounds_actual_stream_and_wav_duration(voice_client):
    client, headers, recognizer = voice_client
    oversized = (chunk for chunk in [b"x" * 600_000, b"y" * 600_000])
    assert transcribe(client, headers["familytest"], content=oversized).status_code == 413
    # This fits the byte ceiling, but its WAV duration exceeds thirty seconds.
    overlong = wav_recording(frames=30 * 16_000 + 1)
    assert len(overlong) < family.MAX_VOICE_BYTES
    assert transcribe(client, headers["familytest"], content=overlong).status_code == 413
    assert recognizer.calls == []
    assert not family._voice_lock.locked()


def test_unconfigured_or_failed_asr_returns_actionable_unavailable_error(voice_client):
    client, headers, recognizer = voice_client
    recognizer.available = False
    unavailable = transcribe(client, headers["familytest"])
    assert unavailable.status_code == 503
    assert "中文语音识别模型" in unavailable.json()["detail"]
    assert recognizer.calls == []
    recognizer.available = True
    recognizer.error = ASRError("model load failed")
    failed = transcribe(client, headers["familytest"])
    assert failed.status_code == 503
    assert "检查本地模型配置" in failed.json()["detail"]
    assert not family._voice_lock.locked()


@pytest.mark.parametrize("text", ["", "   ", "字" * 501])
def test_asr_empty_or_excessive_text_is_not_accepted(voice_client, text):
    client, headers, recognizer = voice_client
    recognizer.text = text
    assert transcribe(client, headers["familytest"]).status_code == 400
    with database.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM family_messages").fetchone()[0] == 0


def test_voice_request_is_rejected_while_another_recording_is_being_processed(voice_client):
    client, headers, recognizer = voice_client
    recognizer.started = asyncio.Event()
    recognizer.release = asyncio.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(transcribe, client, headers["familytest"])
        client.portal.call(asyncio.wait_for, recognizer.started.wait(), 2)
        busy = transcribe(client, headers["familytest"])
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "2"
        assert len(recognizer.calls) == 1
        client.portal.call(recognizer.release.set)
        assert first.result(timeout=3).status_code == 200


def test_transcription_timeout_keeps_slot_until_underlying_inference_finishes(voice_client, monkeypatch):
    client, headers, recognizer = voice_client
    monkeypatch.setattr(family, "VOICE_TRANSCRIBE_TIMEOUT_SECONDS", 0.01)
    recognizer.release = asyncio.Event()
    timed_out = transcribe(client, headers["familytest"])
    assert timed_out.status_code == 504
    assert family._voice_lock.locked()
    assert transcribe(client, headers["familytest"]).status_code == 503
    assert len(recognizer.calls) == 1
    client.portal.call(recognizer.release.set)
    client.portal.call(asyncio.sleep, 0.02)
    assert not family._voice_lock.locked()
    assert transcribe(client, headers["familytest"]).status_code == 200


def test_upload_timeout_releases_slot_without_starting_asr(voice_client, monkeypatch):
    client, headers, recognizer = voice_client

    async def slow_upload(request):
        await asyncio.Event().wait()

    monkeypatch.setattr(family, "_read_voice_upload", slow_upload)
    monkeypatch.setattr(family, "VOICE_UPLOAD_TIMEOUT_SECONDS", 0.01)
    assert transcribe(client, headers["familytest"]).status_code == 408
    assert not family._voice_lock.locked()
    assert recognizer.calls == []
