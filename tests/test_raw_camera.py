import asyncio
from collections import OrderedDict, defaultdict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import auth, database
from backend.camera_stream import camera_frames
from backend.config import settings
from backend.raw_camera import (DEFAULT_DEVICE_ID, FRAME_BYTES, MAX_JPEG_BYTES, RawCameraConnection,
                                grayscale_jpeg, validated_camera_jpeg, router)
from backend.services import Runtime, runtime
from backend.tablet import router as tablet_router
from backend.tts_protocol import speech_command
from backend.vision import AlertController, ApproachDetector, TrackerAdapter

RAW = np.tile(np.arange(320, dtype=np.uint16) % 256, (240, 1)).astype(np.uint8).tobytes()
COMPRESSED = cv2.imencode('.jpg', np.frombuffer(RAW, np.uint8).reshape(240, 320),
                         [cv2.IMWRITE_JPEG_QUALITY, 65])[1].tobytes()


@pytest.fixture
def camera_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "camera.sqlite3"))
    monkeypatch.setattr(runtime, "devices", {})
    monkeypatch.setattr(runtime, "image_queue", asyncio.Queue(maxsize=1))
    monkeypatch.setattr(runtime, "web_clients", set())
    monkeypatch.setattr(runtime, "latest", {})
    monkeypatch.setattr(runtime, "locations", {})
    monkeypatch.setattr(runtime, "motor_monitor_leases", {})
    monkeypatch.setattr(runtime, "speech_enabled", True)
    monkeypatch.setattr(runtime, "tracker", TrackerAdapter())
    monkeypatch.setattr(runtime, "approach_detector", ApproachDetector())
    monkeypatch.setattr(runtime, "alert_controller", AlertController())
    monkeypatch.setattr(runtime, "alert_frame_index", defaultdict(int))
    monkeypatch.setattr(RawCameraConnection, "SPEECH_SECONDS_PER_CHARACTER", 0)
    monkeypatch.setattr(RawCameraConnection, "SPEECH_GAP_SECONDS", 0)
    monkeypatch.setattr(camera_frames, "_frames", OrderedDict())
    database.init_db()
    auth.register_user("cameratest", "camera@example.com", "Camera123")
    token = auth.create_session("cameratest", "Camera123", False)["token"]
    transport_app = FastAPI()
    transport_app.include_router(router)
    transport_app.include_router(tablet_router)
    # Exercise real ASGI sockets but never start the application's real YOLO task.
    with TestClient(transport_app) as client:
        client.camera_offline = asyncio.Event()

        async def record_status(message):
            if message.get("type") == "device_status" and not message.get("online"):
                client.camera_offline.set()

        monkeypatch.setattr(runtime, "broadcast", record_status)
        yield client, {"Authorization": "Bearer " + token}


def test_grayscale_becomes_correct_three_channel_detector_input():
    jpeg = grayscale_jpeg(RAW)
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (240, 320, 3)
    assert np.array_equal(decoded[:, :, 0], decoded[:, :, 2])
    assert np.abs(decoded[:, :, 0].astype(int) - np.frombuffer(RAW, np.uint8).reshape(240, 320)).mean() < 2


@pytest.mark.parametrize("length", [0, FRAME_BYTES - 1, FRAME_BYTES + 1, 100000])
def test_invalid_gray_length_is_rejected(length):
    with pytest.raises(ValueError, match="76800"):
        grayscale_jpeg(b"\0" * length)


def test_device_jpeg_is_preserved_without_a_second_lossy_encode():
    assert validated_camera_jpeg(COMPRESSED) is COMPRESSED
    decoded = cv2.imdecode(np.frombuffer(COMPRESSED, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (240, 320, 3)


def test_color_jpeg_reaches_preview_without_becoming_grayscale(camera_client):
    image = np.zeros((240, 320, 3), np.uint8)
    image[:, :160] = (0, 0, 220)
    image[:, 160:] = (0, 220, 0)
    jpeg = cv2.imencode('.jpg', image)[1].tobytes()
    client, headers = camera_client
    with client.websocket_connect('/ws/camera?protocol=2&format=jpeg') as ws:
        ws.send_bytes(jpeg)
        assert ws.receive_text() == 'ACK'
        queued = runtime.image_queue.get_nowait()[2]
        preview = client.get(f'/api/tablet/cameras/{DEFAULT_DEVICE_ID}/frame', headers=headers)
        assert preview.content == queued == jpeg
        decoded = cv2.imdecode(np.frombuffer(queued, np.uint8), cv2.IMREAD_COLOR)
        assert decoded[120, 80, 2] > decoded[120, 80, 1] + 100
        assert decoded[120, 240, 1] > decoded[120, 240, 2] + 100


@pytest.mark.parametrize('data', [b'', b'not a jpeg', COMPRESSED[:-2],
                                 b'\xff\xd8invalid\xff\xd9',
                                 b'\xff\xd8' + b'x' * MAX_JPEG_BYTES + b'\xff\xd9',
                                 cv2.imencode('.jpg', np.zeros((120, 160), np.uint8))[1].tobytes()],
                         ids=['empty', 'not-jpeg', 'truncated', 'invalid-pixels', 'oversized', 'wrong-size'])
def test_invalid_device_jpeg_is_rejected(data):
    with pytest.raises(ValueError):
        validated_camera_jpeg(data)


def test_compressed_camera_upload_ack_preview_and_recovery(camera_client):
    client, headers = camera_client
    with client.websocket_connect('/ws/camera?protocol=2&format=jpeg') as ws:
        ws.send_bytes(RAW)
        assert ws.receive_text().startswith('ERR:BAD_FRAME:')
        assert runtime.image_queue.empty()
        for expected_seq in (1, 2, 3):
            ws.send_bytes(COMPRESSED)
            assert ws.receive_text() == 'ACK'
        device_id, seq, jpeg = runtime.image_queue.get_nowait()
        assert (device_id, seq, jpeg) == (DEFAULT_DEVICE_ID, 3, COMPRESSED)
        preview = client.get(f'/api/tablet/cameras/{device_id}/frame', headers=headers)
        assert preview.status_code == 200
        assert preview.content == COMPRESSED
        assert preview.headers['x-frame-seq'] == '3'
        client.portal.call(runtime.send_device, device_id,
                           {'type': 'device_command', 'command': 'stop'})
        assert ws.receive_bytes() == b'S'
    client.portal.call(asyncio.wait_for, client.camera_offline.wait(), 2)
    assert camera_frames.get(DEFAULT_DEVICE_ID) is None


def test_raw_camera_frame_ack_preview_and_disconnect_cleanup(camera_client):
    client, headers = camera_client
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        ws.send_bytes(RAW)
        assert ws.receive_text() == "ACK"
        device_id, seq, jpeg = runtime.image_queue.get_nowait()
        assert (device_id, seq) == (DEFAULT_DEVICE_ID, 1)
        assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (240, 320, 3)
        preview = client.get(f"/api/tablet/cameras/{device_id}/frame", headers=headers)
        assert preview.content == jpeg
        assert client.get(f"/api/tablet/cameras/{device_id}/frame").status_code == 401
        with database.db() as c:
            row = dict(c.execute("SELECT * FROM device_info WHERE device_id=?", (device_id,)).fetchone())
        assert row["online"] == row["camera_ok"] == 1
        assert all(row[key] is None for key in ("mic_ok", "left_motor_ok", "right_motor_ok", "speaker_ok"))
    client.portal.call(asyncio.wait_for, client.camera_offline.wait(), 2)
    assert DEFAULT_DEVICE_ID not in runtime.devices
    assert camera_frames.get(DEFAULT_DEVICE_ID) is None
    with database.db() as c:
        assert c.execute("SELECT online FROM device_info WHERE device_id=?", (DEFAULT_DEVICE_ID,)).fetchone()[0] == 0


def test_invalid_message_does_not_disconnect_and_latest_frame_replaces_queue(camera_client):
    client, _ = camera_client
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        ws.send_bytes(b"\0" * 42)
        assert ws.receive_text().startswith("ERR:BAD_FRAME:")
        ws.send_text("not a frame")
        assert ws.receive_text() == "ERR:EXPECTED_BINARY_QVGA_OR_WIFI_SCAN"
        assert runtime.image_queue.empty()
        for _ in range(3):
            ws.send_bytes(RAW)
            assert ws.receive_text() == "ACK"
        assert runtime.image_queue.qsize() == 1
        assert runtime.image_queue.get_nowait()[1] == 3


@pytest.mark.parametrize("protocol,both", [(1, [b"L", b"R"]), (2, [b"B"])])
def test_real_runtime_alerts_and_manual_commands_use_binary(camera_client, protocol, both):
    client, _ = camera_client
    client.portal.call(runtime.set_motor_monitor, "test", DEFAULT_DEVICE_ID, True)
    with client.websocket_connect(f"/ws/camera?protocol={protocol}") as ws:
        # Barrier also proves that the original bare-gray upload remains supported.
        ws.send_bytes(RAW)
        if protocol == 2:
            assert ws.receive_text() == "ACK"
        else:
            ws.send_bytes(b"bad")
            assert ws.receive_text().startswith("ERR:BAD_FRAME:")
        for direction, expected in [("left", [b"L"]), ("right", [b"R"]), ("both", both)]:
            client.portal.call(runtime.send_device, DEFAULT_DEVICE_ID,
                                   {"type": "obstacle_alert", "device_id": DEFAULT_DEVICE_ID,
                                    "vibration": direction})
            assert [ws.receive_bytes() for _ in expected] == expected
        client.portal.call(runtime.send_device, DEFAULT_DEVICE_ID,
                           {"type": "device_command", "command": "vibrate", "direction": "left"})
        assert ws.receive_bytes() == b"L"
        if protocol == 2:
            client.portal.call(runtime.send_device, DEFAULT_DEVICE_ID,
                               {"type": "device_command", "command": "stop"})
            assert ws.receive_bytes() == b"S"


def test_obstacle_alert_forwards_chinese_speech_to_the_camera_device(camera_client):
    client, _ = camera_client
    client.portal.call(runtime.set_motor_monitor, "test", DEFAULT_DEVICE_ID, True)
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        ws.send_bytes(RAW)
        assert ws.receive_text() == "ACK"
        runtime.image_queue.get_nowait()
        text = "左侧有行人"
        client.portal.call(runtime.send_device, DEFAULT_DEVICE_ID,
                           {"type": "obstacle_alert", "device_id": DEFAULT_DEVICE_ID,
                            "vibration": "left", "voice_text": text})
        assert ws.receive_bytes() == b"L"
        assert ws.receive_bytes() == speech_command(text)


def test_disabling_hardware_speech_keeps_vibration_alert(monkeypatch):
    monkeypatch.setattr(runtime, "speech_enabled", False)
    monkeypatch.setattr(runtime, "motor_monitor_enabled", lambda device_id: True)
    class Socket:
        sent = None

        async def send_bytes(self, value):
            self.sent = value

    socket = Socket()
    asyncio.run(RawCameraConnection(socket, protocol=2).send_json(
        {"type": "obstacle_alert", "vibration": "left", "voice_text": "左侧有行人"}))
    assert socket.sent == b"L"


def test_obstacle_alert_does_not_vibrate_until_hat_preview_is_started(monkeypatch):
    monkeypatch.setattr(runtime, "motor_monitor_leases", {})
    monkeypatch.setattr(runtime, "speech_enabled", False)

    class Socket:
        sent = None

        async def send_bytes(self, value):
            self.sent = value

    socket = Socket()
    asyncio.run(RawCameraConnection(socket, protocol=2).send_json(
        {"type": "obstacle_alert", "device_id": DEFAULT_DEVICE_ID,
         "vibration": "both", "voice_text": None}))
    assert socket.sent is None


def test_hardware_speech_queue_preserves_order_and_serializes_messages(monkeypatch):
    monkeypatch.setattr(runtime, "speech_enabled", True)
    monkeypatch.setattr(RawCameraConnection, "SPEECH_SECONDS_PER_CHARACTER", 0)
    monkeypatch.setattr(RawCameraConnection, "SPEECH_GAP_SECONDS", 0)

    class Socket:
        def __init__(self):
            self.sent = []

        async def send_bytes(self, value):
            self.sent.append(value)

        async def close(self, **kwargs):
            pass

    async def run():
        socket = Socket()
        connection = RawCameraConnection(socket, protocol=2)
        connection._queue_speech("左侧有行人")
        connection._queue_speech("右侧有椅子")
        await asyncio.wait_for(connection._speech_queue.join(), timeout=2)
        await connection.close()
        return socket.sent

    assert asyncio.run(run()) == [speech_command("左侧有行人"), speech_command("右侧有椅子")]


def test_hardware_speech_waits_for_estimated_playback_then_two_seconds():
    connection = RawCameraConnection(object(), protocol=2)
    assert connection._speech_delay("左侧有行人") == 4.0


def test_gray_frames_run_through_detector_and_trigger_trend_alerts(camera_client, monkeypatch):
    client, _ = camera_client

    class DeterministicModel:
        names = {0: "person"}
        index = 0

        def predict(self, image, **kwargs):
            assert image.shape == (240, 320, 3)
            boxes = [
                (120, 60, 160, 120),
                (100, 40, 180, 160),
                (80, 20, 200, 200),
                (60, 0, 220, 230),
                (40, 0, 240, 240),
                (30, 0, 250, 240),
                (20, 0, 260, 240),
            ]
            x1, y1, x2, y2 = boxes[self.index]
            self.index += 1
            box = SimpleNamespace(xyxy=np.array([[x1, y1, x2, y2]]),
                                  conf=np.array([0.95]), cls=np.array([0]),
                                  id=np.array([1]))
            return [SimpleNamespace(boxes=[box])]

    monkeypatch.setattr(runtime, "model", DeterministicModel())
    monkeypatch.setattr(runtime, "model_error", None)
    monkeypatch.setattr(runtime, "alert_last", {})
    monkeypatch.setattr(runtime, "speech_announced", {})
    client.portal.call(runtime.set_motor_monitor, "test", DEFAULT_DEVICE_ID, True)
    monkeypatch.setattr(settings, "simulation_mode", False)
    worker = client.portal.start_task_soon(runtime.detect_loop)
    try:
        with client.websocket_connect("/ws/camera?protocol=2") as ws:
            expected_message_counts = [1, 1, 1, 2, 1, 1, 3]
            for index, message_count in enumerate(expected_message_counts):
                ws.send_bytes(RAW)
                messages = [ws.receive() for _ in range(message_count)]
                assert any(item.get("text") == "ACK" for item in messages)
                commands = [item.get("bytes") for item in messages if item.get("bytes")]
                if index == 3:
                    assert b"B" in commands
                    assert not any(item.startswith(b"T") for item in commands)
                elif index == 6:
                    assert b"B" in commands
                    assert speech_command("前方有行人，请注意") in commands
                else:
                    assert commands == []
    finally:
        worker.cancel()


def test_reconnect_cannot_be_removed_by_old_socket_cleanup(camera_client):
    client, _ = camera_client
    older_context = client.websocket_connect("/ws/camera?protocol=2")
    older = older_context.__enter__()
    try:
        older.send_bytes(RAW)
        assert older.receive_text() == "ACK"
        previous = runtime.devices[DEFAULT_DEVICE_ID]
        with client.websocket_connect("/ws/camera?protocol=2") as newer:
            newer.send_bytes(RAW)
            assert newer.receive_text() == "ACK"
            assert runtime.devices[DEFAULT_DEVICE_ID] is not previous
            older_context.__exit__(None, None, None)
            older_context = None
            newer.send_bytes(RAW)
            assert newer.receive_text() == "ACK"
            assert camera_frames.get(DEFAULT_DEVICE_ID) is not None
            client.portal.call(runtime.set_motor_monitor, "test", DEFAULT_DEVICE_ID, True)
            client.portal.call(runtime.send_device, DEFAULT_DEVICE_ID,
                               {"type": "obstacle_alert", "device_id": DEFAULT_DEVICE_ID,
                                "vibration": "left"})
            assert newer.receive_bytes() == b"L"
    finally:
        if older_context is not None:
            older_context.__exit__(None, None, None)


@pytest.mark.parametrize("query", ["device_id=local_camera", "device_id=tablet_123",
                                   "device_id=simulator_001", "device_id=bad/id", "protocol=3", "format=png"])
def test_invalid_or_reserved_identity_is_rejected(camera_client, query):
    client, _ = camera_client
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/camera?" + query):
            pass
    assert not runtime.devices


def test_wifi_scan_is_processed_asynchronously_and_location_endpoint_is_authenticated(camera_client, monkeypatch):
    client, headers = camera_client

    async def fake_locate(access_points):
        assert access_points == [{"macAddress": "AA:BB:CC:DD:EE:FF", "signalStrength": -45}]
        return {"available": True, "status": "located", "latitude": 31.2,
                "longitude": 121.5, "accuracy_m": 85, "provider": "BeaconDB",
                "access_points_used": 2, "updated_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr("backend.raw_camera.locate_wifi", fake_locate)
    endpoint = f"/api/tablet/location?device_id={DEFAULT_DEVICE_ID}"
    assert client.get(endpoint).status_code == 401
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        ws.send_text('{"type":"wifi_scan","access_points":[{"macAddress":"AA:BB:CC:DD:EE:FF","signalStrength":-45}]}')
        assert ws.receive_text() == "WIFI_SCAN_RECEIVED"
        ws.send_bytes(RAW)
        assert ws.receive_text() == "ACK"
        client.portal.call(asyncio.sleep, 0.05)
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200
        assert response.json()["latitude"] == 31.2
        assert response.json()["status"] == "located"


def test_wifi_scan_rejects_malformed_payload_without_dropping_camera(camera_client):
    client, _ = camera_client
    with client.websocket_connect("/ws/camera?protocol=2") as ws:
        ws.send_text('{"type":"wifi_scan","access_points":"bad"}')
        assert ws.receive_text() == "ERR:BAD_WIFI_SCAN"
        ws.send_bytes(RAW)
        assert ws.receive_text() == "ACK"


def test_send_failure_does_not_remove_a_newer_connection():
    instance = Runtime()
    replacement = object()

    class OldConnection:
        async def send_json(self, message):
            instance.devices["hat"] = replacement
            raise OSError("old connection closed")

    instance.devices["hat"] = OldConnection()
    asyncio.run(instance.send_device("hat", {}))
    assert instance.devices["hat"] is replacement
