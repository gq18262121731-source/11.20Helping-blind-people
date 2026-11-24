from fastapi.testclient import TestClient

from backend.main import app


def test_audio_worklet_processor_is_served_without_cache():
    response = TestClient(app).get("/voice-input-worklet.js")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "registerProcessor('qiming-pcm-capture'" in response.text
