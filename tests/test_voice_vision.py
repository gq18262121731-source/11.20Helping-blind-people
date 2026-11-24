import io
import asyncio

from fastapi.testclient import TestClient
from PIL import Image

from backend import auth, database, main, voice_vision
from backend.config import settings
from backend.intent import Intent, IntentClassifier
from backend.vision_interpretation import VLMServiceError


def jpeg_bytes():
    output = io.BytesIO()
    Image.new("RGB", (48, 32), "white").save(output, format="JPEG")
    return output.getvalue()


def setup_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "voice-vision.sqlite3"))
    database.init_db()
    auth.register_user("visionuser", "vision@example.com", "Vision123")
    token = auth.create_session("visionuser", "Vision123", False)["token"]
    return {"Authorization": "Bearer " + token}


def test_intent_classifier_routes_scene_description():
    match = IntentClassifier().classify("请描述一下当前画面")
    assert match.intent is Intent.SCENE_DESCRIBE
    assert match.confidence > 0.8


def test_scene_task_combines_same_frame_detection_and_vlm_and_persists(tmp_path, monkeypatch):
    headers = setup_auth(tmp_path, monkeypatch)

    async def describe(image, question=""):
        assert image == jpeg_bytes()
        return {"scene_description": "画面中有一处开阔的人行通道。", "model": "test-vlm", "latency_ms": 12.5, "status": "ok"}

    async def detect(image, source_id="voice_task"):
        assert image == jpeg_bytes()
        return {"objects": [{"class": "person", "direction": "left", "confidence": 0.95}], "model_status": "ready", "source_id": source_id}

    monkeypatch.setattr(voice_vision, "describe_scene", describe)
    monkeypatch.setattr(main, "detect_image_once", detect)
    client = TestClient(main.app)
    try:
        response = client.post(
            "/api/voice-vision/tasks?intent=SCENE_DESCRIBE&text=%E6%8F%8F%E8%BF%B0%E7%94%BB%E9%9D%A2&source_id=upload&source_mode=software",
            content=jpeg_bytes(), headers={**headers, "Content-Type": "image/jpeg"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["intent"] == "SCENE_DESCRIBE"
        assert result["detection_snapshot"][0]["class"] == "person"
        assert result["vlm_scene_description"] == "画面中有一处开阔的人行通道。"
        assert "左侧有行人" in result["final_reply"]
        assert "开阔的人行通道" in result["final_reply"]
        history = client.get("/api/voice-vision/tasks?source_mode=software", headers=headers)
        assert history.status_code == 200
        assert history.json()[0]["intent"] == "SCENE_DESCRIBE"
        tts = client.patch(
            f"/api/voice-vision/tasks/{result['task_id']}/tts",
            json={"status": "completed", "target": "browser"}, headers=headers,
        )
        assert tts.status_code == 200
        assert tts.json()["tts_status"] == "completed"
    finally:
        client.close()


def test_ocr_task_returns_rapidocr_box_coordinates_to_the_reader(tmp_path, monkeypatch):
    headers = setup_auth(tmp_path, monkeypatch)

    async def recognize(image):
        assert image == jpeg_bytes()
        return {
            "type": "ocr_result", "text": "出口", "has_text": True,
            "truncated": False, "model": "test-rapidocr",
            "boxes": [{"text": "出口", "points": [[5, 6], [25, 6], [25, 18], [5, 18]]}],
            "image_width": 48, "image_height": 32,
        }

    monkeypatch.setattr(voice_vision, "recognize_text", recognize)
    client = TestClient(main.app)
    try:
        response = client.post(
            "/api/voice-vision/tasks?intent=OCR_READ&text=读一下画面里的文字&source_id=upload&source_mode=software",
            content=jpeg_bytes(), headers={**headers, "Content-Type": "image/jpeg"},
        )
        assert response.status_code == 200, response.text
        task = response.json()
        assert task["ocr_result"]["boxes"][0]["text"] == "出口"
        assert task["ocr_result"]["image_width"] == 48
        assert task["ocr_result"]["image_height"] == 32
    finally:
        client.close()


def test_unknown_task_is_recorded_and_api_requires_login(tmp_path, monkeypatch):
    headers = setup_auth(tmp_path, monkeypatch)
    client = TestClient(main.app)
    try:
        assert client.get("/api/voice-vision/capabilities").status_code == 401
        response = client.post(
            "/api/voice-vision/tasks",
            json={"text": "随便说点什么", "source_id": "local_camera", "source_mode": "software"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["intent"] == "UNKNOWN"
        assert response.json()["error_code"] == "UNKNOWN_INTENT"
        assert client.get("/api/voice-vision/tasks", headers=headers).json()[0]["intent"] == "UNKNOWN"
    finally:
        client.close()


def test_hat_speak_endpoint_requires_hardware_task_and_queues_existing_reply(tmp_path, monkeypatch):
    headers = setup_auth(tmp_path, monkeypatch)
    task_id = main._store_task("前面有什么？", {
        "intent": "ENV_QUERY", "intent_confidence": 0.9, "source_id": "hat-001",
        "source_mode": "hardware", "device_id": "hat-001", "final_reply": "前方有行人。",
        "response_basis": ["yolo_detection"], "detection_snapshot": [], "stage_latency_ms": {},
        "tts_target": "device", "tts_status": "not_requested",
    })
    monkeypatch.setattr(main, "_queue_device_speech", lambda task, device, text: {
        "status": "queued", "task_id": task, "phrase_id": "front_person", "request_id": 44,
    })
    client = TestClient(main.app)
    try:
        response = client.post(f"/api/voice-vision/tasks/{task_id}/speak", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        invalid_task = main._store_task("描述画面", {
            "intent": "SCENE_DESCRIBE", "intent_confidence": 1, "source_id": "upload",
            "source_mode": "software", "device_id": None, "final_reply": "一个人行通道。",
            "response_basis": ["vlm_scene"], "detection_snapshot": [], "stage_latency_ms": {},
            "tts_target": "browser", "tts_status": "not_requested",
        })
        invalid = client.post(f"/api/voice-vision/tasks/{invalid_task}/speak", headers=headers)
        assert invalid.status_code == 409
    finally:
        client.close()


def test_environment_query_uses_detector_when_vlm_is_unavailable(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise VLMServiceError(503, "模型未配置", "VLM_UNAVAILABLE")
    monkeypatch.setattr(voice_vision, "describe_scene", unavailable)
    result = asyncio.run(voice_vision.execute_task(
        "前面有什么", image=jpeg_bytes(), frame_seq=19,
        detection_result={"model_status": "ready", "objects": [{"class": "person", "direction": "front", "confidence": 0.9}]},
    ))
    assert result["intent"] == "ENV_QUERY"
    assert result["vlm_status"] == "unavailable"
    assert result["reply_status"] == "partial"
    assert "前方有行人" in result["final_reply"]
    assert result["frame_seq"] == 19


def test_control_intents_do_not_call_visual_models(monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("Control intents must not call OCR or VLM")
    monkeypatch.setattr(voice_vision, "describe_scene", unexpected)
    monkeypatch.setattr(voice_vision, "recognize_text", unexpected)
    repeated = asyncio.run(voice_vision.execute_task("再读一遍", previous_ocr="出口"))
    assert repeated["final_reply"] == "出口"
    stopped = asyncio.run(voice_vision.execute_task("停止", image=jpeg_bytes()))
    assert stopped["intent"] == "STOP"
    assert stopped["vlm_status"] == "not_needed"


def test_cannot_fake_device_playback_receipt(tmp_path, monkeypatch):
    headers = setup_auth(tmp_path, monkeypatch)
    client = TestClient(main.app)
    try:
        result = client.patch("/api/voice-vision/tasks/1/tts", json={"target": "device", "status": "completed"}, headers=headers)
        assert result.status_code == 422
        assert client.post("/api/voice-vision/tasks", json=[], headers=headers).status_code == 422
        assert client.post("/api/voice-vision/tasks", json={"text": "前面有什么", "source_id": "local_camera", "source_mode": "hardware"}, headers=headers).status_code == 422
    finally:
        client.close()


def test_audio_output_preserves_user_transcript(tmp_path, monkeypatch):
    from types import SimpleNamespace
    setup_auth(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_queue_device_speech", lambda *args: {"status": "device_offline"})
    session = SimpleNamespace(start=SimpleNamespace(device_id="simulator_test"))
    match = IntentClassifier().classify("再读一遍")
    result = asyncio.run(main._voice_intent_handler(match, session))
    assert "text" not in result
    assert result["reply"] == "还没有上一条识别结果。"
    assert result["intent"] == "OCR_AGAIN"
