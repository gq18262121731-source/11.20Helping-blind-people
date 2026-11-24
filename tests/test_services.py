import asyncio
import json
from backend.camera_stream import camera_frames
from backend.config import settings
from backend.services import Runtime, runtime

def test_direction_boundaries():
    assert runtime.direction(10,100) == "left"
    assert runtime.direction(50,100) == "front"
    assert runtime.direction(90,100) == "right"


def test_latest_frame_queue_carries_timestamp_for_its_own_sequence(monkeypatch):
    detector = Runtime()
    monkeypatch.setattr(camera_frames, "_frames", {})
    clock_value = [100.0]
    def fake_clock():
        clock_value[0] += 0.01
        return clock_value[0]
    monkeypatch.setattr(camera_frames, "_clock", fake_clock)
    camera_frames.publish("local_camera", 10, b"\xff\xd8old\xff\xd9")
    first = detector.enqueue_frame("local_camera", 10, b"\xff\xd8old\xff\xd9")
    old = detector.image_queue.get_nowait()
    old_timestamp = detector.frame_timestamps[("local_camera", 10)]
    detector.image_queue.task_done()
    camera_frames.publish("local_camera", 11, b"\xff\xd8new\xff\xd9")
    assert detector.enqueue_frame("local_camera", 10, b"\xff\xd8old\xff\xd9") is False
    assert detector.enqueue_frame("local_camera", 11, b"\xff\xd8new\xff\xd9") is True
    queued = detector.image_queue.get_nowait()
    assert first is True
    assert queued[1] == 11
    assert detector.frame_timestamps[("local_camera", 11)] == camera_frames.get("local_camera").received_at
    assert detector.frame_timestamps[("local_camera", 11)] != old_timestamp
    detector.image_queue.task_done()


def test_latest_frame_queue_drops_oldest_when_backlogged(monkeypatch):
    detector = Runtime()
    monkeypatch.setattr(camera_frames, "_frames", {})
    camera_frames.publish("local_camera", 1, b"\xff\xd8old\xff\xd9")
    detector.enqueue_frame("local_camera", 1, b"\xff\xd8old\xff\xd9")
    camera_frames.publish("local_camera", 2, b"\xff\xd8new\xff\xd9")
    detector.enqueue_frame("local_camera", 2, b"\xff\xd8new\xff\xd9")
    queued = detector.image_queue.get_nowait()
    assert queued[1:3] == (2, b"\xff\xd8new\xff\xd9")
    assert detector.image_queue.qsize() == 0
    detector.image_queue.task_done()


def test_empty_yolo_predictions_are_a_valid_empty_frame(monkeypatch):
    import cv2
    import numpy as np

    class EmptyModel:
        names = {0: "person"}
        def predict(self, image, **kwargs):
            return []

    detector = Runtime()
    detector.model = EmptyModel()
    detector.model_error = None
    detector.tracking_enabled = False
    detector.alerts_enabled = False
    detector.depth_error = "disabled_by_config"
    monkeypatch.setattr(settings, "depth_enabled", False)
    monkeypatch.setattr(camera_frames, "_frames", {})
    _, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    image = encoded.tobytes()

    async def process_one_frame():
        camera_frames.publish("empty_probe", 1, image)
        assert detector.enqueue_frame("empty_probe", 1, image)
        task = asyncio.create_task(detector.detect_loop())
        try:
            for _ in range(100):
                latest = detector.latest.get("empty_probe")
                if latest:
                    return latest["result"]
                await asyncio.sleep(0.01)
            raise AssertionError("detector did not publish the empty frame result")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    result = asyncio.run(process_one_frame())
    assert result["model_status"] == "ready"
    assert result["objects"] == []


def test_yolo_result_iterator_index_error_skips_frame_without_disabling_detection(monkeypatch):
    import cv2
    import numpy as np

    class MalformedResults:
        def __iter__(self):
            raise IndexError("list index out of range")

    class MalformedModel:
        names = {0: "person"}

        def predict(self, image, **kwargs):
            return MalformedResults()

    detector = Runtime()
    detector.model = MalformedModel()
    detector.model_error = None
    detector.tracking_enabled = False
    detector.alerts_enabled = False
    monkeypatch.setattr(settings, "depth_enabled", False)
    monkeypatch.setattr(camera_frames, "_frames", {})
    _, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    image = encoded.tobytes()

    async def process_one_frame():
        camera_frames.publish("malformed_probe", 1, image)
        assert detector.enqueue_frame("malformed_probe", 1, image)
        task = asyncio.create_task(detector.detect_loop())
        try:
            for _ in range(100):
                latest = detector.latest.get("malformed_probe")
                if latest:
                    return latest["result"]
                await asyncio.sleep(0.01)
            raise AssertionError("detector did not publish its recovered frame")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    result = asyncio.run(process_one_frame())
    assert result["model_status"] == "ready"
    assert result["objects"] == []
    assert result["detection_warning"] == "结果帧已跳过：IndexError"


def test_detected_object_speech_waits_for_confirmation_and_repeats_after_target_leaves():
    class Speaker:
        def __init__(self):
            self.messages = []

        def speak_text(self, text):
            self.messages.append(text)
            return True

    detector = Runtime()
    speaker = Speaker()
    detector.devices["hat"] = speaker
    person = [{"class": "person", "confidence": 0.9, "direction": "left", "track_id": 1}]

    assert detector.announce_objects("hat", person, now=1.0) is False
    assert detector.announce_objects("hat", person, now=1.5) is True
    assert detector.announce_objects("hat", person, now=2.0) is False
    assert speaker.messages == ["左侧有行人。"]

    detector.announce_objects("hat", [], now=6.0)
    assert detector.announce_objects("hat", person, now=6.5) is False
    assert detector.announce_objects("hat", person, now=7.0) is True
    assert speaker.messages == ["左侧有行人。", "左侧有行人。"]


def test_device_disconnect_cleans_up_session(tmp_path, monkeypatch):
    from backend.database import init_db
    from backend.main import device_ws

    monkeypatch.setattr(settings, "database_path", str(tmp_path / "device.sqlite3"))
    init_db()
    monkeypatch.setattr(runtime, "devices", {})
    events = []

    async def record_event(event):
        events.append(event)

    monkeypatch.setattr(runtime, "broadcast", record_event)

    class DeviceSocket:
        def __init__(self):
            self.messages = iter([
                {"type": "websocket.receive", "text": json.dumps({"type": "device_online", "device_id": "disconnect_check"})},
                {"type": "websocket.disconnect", "code": 1000},
            ])

        async def accept(self):
            pass

        async def receive(self):
            return next(self.messages)

    asyncio.run(device_ws(DeviceSocket()))
    assert "disconnect_check" not in runtime.devices
    assert [event["online"] for event in events] == [True, False]
