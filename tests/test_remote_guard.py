import asyncio
from collections import OrderedDict

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth, database, remote_guard
from backend.camera_stream import camera_frames
from backend.asr import ASRResult
from backend.config import settings
from backend.services import runtime


@pytest.fixture
def guard_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "guard.sqlite3"))
    monkeypatch.setattr(settings, "amap_web_key", "")
    monkeypatch.setattr(settings, "amap_js_key", "")
    monkeypatch.setattr(settings, "map_service_mode", "cocomap")
    monkeypatch.setattr(settings, "cocomap_api_key", "")
    monkeypatch.setattr(settings, "cocomap_base_url", "https://map.culture09.xyz")
    monkeypatch.setattr(settings, "map_js_provider", "baidu")
    monkeypatch.setattr(settings, "baidu_map_js_ak", "")
    monkeypatch.setattr(settings, "map_demo_mode", True)
    monkeypatch.setattr(runtime, "locations", {})
    remote_guard._guard_clients.clear()
    remote_guard._navigations.clear()
    for task in remote_guard._demo_tasks.values():
        if not task.done():
            task.cancel()
    remote_guard._demo_tasks.clear()
    monkeypatch.setattr(camera_frames, "_frames", OrderedDict())
    database.init_db()
    headers = {}
    for username in ("guardowner", "guardfamily", "guardother"):
        auth.register_user(username, username + "@example.com", "Guard123")
        headers[username] = {"Authorization": "Bearer " + auth.create_session(username, "Guard123", False)["token"]}
    app = FastAPI()
    app.include_router(remote_guard.router)
    with TestClient(app) as client:
        created = client.post("/api/v1/guard/devices", headers=headers["guardowner"], json={"name": "测试帽端"})
        assert created.status_code == 200
        headers["device_id"] = created.json()["device_id"]
        with database.db() as connection:
            connection.execute(
                "INSERT INTO device_family_bindings(device_id,owner_username,family_username,can_view_camera,can_view_location,can_send_messages,created_at) VALUES(?,?,?,1,1,0,?)",
                (headers["device_id"], "guardowner", "guardfamily", database.now_iso()),
            )
        yield client, headers


def report(client, headers, **values):
    return client.post("/api/v1/location/report", headers=headers["guardowner"], json={
        "device_id": headers["device_id"], "latitude": 31.2304, "longitude": 121.4737,
        "accuracy": 18, "source": "browser_geolocation", **values,
    })


def test_location_requires_device_authorization_and_valid_provider(guard_client):
    client, headers = guard_client
    path = f"/api/v1/location/latest?device_id={headers['device_id']}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers["guardother"]).status_code == 403
    assert report(client, headers, source="gnss").status_code == 422
    assert report(client, headers, latitude="NaN").status_code == 422
    accepted = report(client, headers)
    assert accepted.status_code == 200
    point = accepted.json()["location"]
    assert point["source"] == "browser_geolocation"
    assert point["mode"] == "real"
    latest = client.get(path, headers=headers["guardfamily"]).json()
    assert latest["available"] is True
    history = client.get(f"/api/v1/location/history?device_id={headers['device_id']}", headers=headers["guardfamily"])
    assert len(history.json()) == 1


def test_sharing_pause_blocks_family_location_and_video(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    report(client, headers)
    assert client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardfamily"]).status_code == 200
    paused = client.post("/api/v1/sharing/stop", headers=headers["guardowner"], json={"device_id": device_id})
    assert paused.json()["sharing"] is False
    assert client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardfamily"]).status_code == 403
    assert client.get(f"/api/v1/video/mjpeg?device_id={device_id}", headers=headers["guardfamily"]).status_code == 403
    assert client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardowner"]).status_code == 200


def test_mock_map_search_and_navigation_progress_are_explicit(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    report(client, headers)
    results = client.post("/api/v1/map/nearby", headers=headers["guardowner"], json={"device_id": device_id, "keyword": "医院"})
    assert results.status_code == 200
    assert results.json()["mode"] == "mock"
    assert results.json()["results"][0]["name"] == "医院示例地点"
    assistant = client.post("/api/v1/map/assistant", headers=headers["guardowner"], json={"device_id": device_id, "text": "附近有没有医院？"})
    assert assistant.json()["tool"] == "search_nearby"
    assert assistant.json()["reply"].startswith("演示数据：")


def test_nearby_assistant_query_bypasses_optional_llm_place_search(guard_client, monkeypatch):
    client, headers = guard_client
    device_id = headers["device_id"]
    report(client, headers)
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.example.test")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_model", "test-model")

    class ForbiddenClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self):
            pytest.fail("附近查询不应调用可选 LLM")
        async def __aexit__(self, *_args): pass

    monkeypatch.setattr(remote_guard.httpx, "AsyncClient", ForbiddenClient)
    assistant = client.post("/api/v1/map/assistant", headers=headers["guardowner"], json={"device_id": device_id, "text": "附近有没有医院？"})
    assert assistant.status_code == 200
    assert assistant.json()["tool"] == "search_nearby"
    assert assistant.json()["reply"].startswith("演示数据：")


def test_current_location_assistant_reply_omits_provider_label(guard_client, monkeypatch):
    client, headers = guard_client
    device_id = headers["device_id"]
    report(client, headers)

    async def reverse(_payload, _authorization):
        return {"mode": "amap", "address": "南京市栖霞区", "district": "栖霞区", "street": ""}

    monkeypatch.setattr(remote_guard, "reverse_geocode", reverse)
    assistant = client.post("/api/v1/map/assistant", headers=headers["guardowner"], json={"device_id": device_id, "text": "我现在在哪个街区？"})
    assert assistant.status_code == 200
    assert assistant.json()["reply"] == "南京市栖霞区。"

    destination = {"latitude": 31.2314, "longitude": 121.4747}
    route = client.post("/api/v1/map/walking-route", headers=headers["guardowner"], json={"device_id": device_id, "destination": destination}).json()
    assert route["status"] == "ROUTE_READY"
    assert route["route"]["mode"] == "mock"
    assert client.get(f"/api/v1/navigation/status?device_id={device_id}", headers=headers["guardfamily"]).json()["status"] == "IDLE"
    midpoint = report(client, headers, latitude=31.2309, longitude=121.4742).json()["location"]
    advanced = client.get(f"/api/v1/navigation/status?device_id={device_id}", headers=headers["guardowner"]).json()
    assert advanced["status"] == "NAVIGATING"
    assert advanced["progress"] > 0
    arrived = report(client, headers, latitude=destination["latitude"], longitude=destination["longitude"]).json()
    status = client.get(f"/api/v1/navigation/status?device_id={device_id}", headers=headers["guardowner"]).json()
    assert status["status"] == "ARRIVED"


def test_stale_location_pauses_nearby_search_and_navigation(guard_client, monkeypatch):
    client, headers = guard_client
    device_id = headers["device_id"]
    report(client, headers)
    client.post("/api/v1/map/walking-route", headers=headers["guardowner"], json={"device_id": device_id, "destination": {"latitude": 31.232, "longitude": 121.475}})
    monkeypatch.setattr(remote_guard, "LOCATION_MAX_AGE_SECONDS", -1)
    assert client.post("/api/v1/map/nearby", headers=headers["guardowner"], json={"device_id": device_id}).status_code == 409
    status = client.get(f"/api/v1/navigation/status?device_id={device_id}", headers=headers["guardowner"]).json()
    assert status["status"] == "PAUSED"


def test_sensitive_location_events_only_go_to_device_authorized_sockets(guard_client):
    class Socket:
        def __init__(self): self.events = []
        async def send_json(self, event): self.events.append(event)
    device_id = guard_client[1]["device_id"]
    authorized, unrelated = Socket(), Socket()
    remote_guard._guard_clients[device_id] = {authorized}
    remote_guard._guard_clients["other_device"] = {unrelated}
    remote_guard._guard_sessions[authorized] = ("guardowner", device_id, True, "valid-token")
    asyncio.run(remote_guard._publish(device_id, {"type":"location_update","location":{"latitude":1}}))
    assert len(authorized.events) == 0  # an unregistered session token is rejected
    assert unrelated.events == []


def test_guard_websocket_requires_account_session_and_receives_only_bound_device_events(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    token = headers["guardfamily"]["Authorization"].split(" ",1)[1]
    with client.websocket_connect(f"/ws/v1/guard/{device_id}") as socket:
        socket.send_json({"type":"authenticate","token":token})
        assert report(client,headers).status_code == 200
        event = socket.receive_json()
        assert event["type"] == "location_update"
        assert event["location"]["device_id"] == device_id

        assert client.post("/api/v1/sharing/stop",headers=headers["guardowner"],json={"device_id":device_id}).json()["sharing"] is False
        with pytest.raises(Exception):
            socket.receive_json()


def test_existing_asr_transcribes_map_audio_without_message_permission(guard_client, monkeypatch):
    import io
    import wave
    client, headers = guard_client

    class FakeASR:
        available = True
        async def recognize_wav(self, data): return ASRResult("附近有没有医院？")

    monkeypatch.setattr(remote_guard, "asr", FakeASR())
    audio = io.BytesIO()
    with wave.open(audio, "wb") as recording:
        recording.setnchannels(1); recording.setsampwidth(2); recording.setframerate(16_000)
        recording.writeframes(b"\x00\x01" * 3200)
    response = client.post(
        f"/api/v1/map/voice/transcribe?device_id={headers['device_id']}",
        headers={**headers["guardfamily"], "Content-Type":"audio/wav"},
        content=audio.getvalue(),
    )
    assert response.status_code == 200
    assert response.json() == {"text":"附近有没有医院？","source":"existing_asr","requires_confirmation":True}


def test_amap_coordinate_adapter_round_trip():
    original = (31.2304, 121.4737)
    gcj = remote_guard._wgs84_to_gcj02(*original)
    restored = remote_guard._gcj02_to_wgs84(*gcj)
    assert restored == pytest.approx(original, abs=1e-6)


def test_api_returns_authorized_real_events_and_requires_video_session(guard_client):
    client, headers = guard_client
    device_id=headers["device_id"]
    report(client,headers,source="mock",mode="demo")
    events=client.get(f"/api/v1/events?device_id={device_id}",headers=headers["guardfamily"])
    assert events.status_code==200
    assert events.json()==[]  # demo-provider locations never enter the family timeline
    assert client.get(f"/api/v1/video/mjpeg?device_id={device_id}").status_code==401
    assert client.post(f"/api/v1/map/voice/transcribe?device_id={device_id}",headers={**headers["guardfamily"],"Content-Type":"text/plain"},content="x").status_code==415


def test_provider_switch_starts_a_new_location_and_navigation_session(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    assert report(client, headers).status_code == 200
    route = client.post("/api/v1/map/walking-route", headers=headers["guardowner"], json={
        "device_id": device_id, "destination": {"latitude": 31.232, "longitude": 121.475},
    })
    assert route.status_code == 200
    switched = client.post("/api/v1/location/provider", headers=headers["guardowner"], json={
        "device_id": device_id, "source": "mock", "enabled": True,
    })
    assert switched.status_code == 200
    latest = client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardowner"]).json()
    assert latest["available"] is False
    assert client.get(f"/api/v1/navigation/status?device_id={device_id}", headers=headers["guardowner"]).json()["status"] == "IDLE"
    assert client.get(f"/api/v1/location/history?device_id={device_id}", headers=headers["guardowner"]).json() == []
    assert report(client, headers, source="mock", mode="demo").status_code == 200
    latest = client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardowner"]).json()
    assert latest["location"]["mode"] == "demo"
    assert client.get(f"/api/v1/location/latest?device_id={device_id}", headers=headers["guardfamily"]).json()["available"] is False


def test_family_map_tools_reject_demo_location(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    assert client.post("/api/v1/location/provider", headers=headers["guardowner"], json={
        "device_id": device_id, "source": "mock", "enabled": True,
    }).status_code == 200
    assert report(client, headers, source="mock", mode="demo").status_code == 200
    nearby = client.post("/api/v1/map/nearby", headers=headers["guardfamily"], json={"device_id": device_id, "keyword": "医院"})
    assert nearby.status_code == 409
    route = client.post("/api/v1/map/walking-route", headers=headers["guardfamily"], json={
        "device_id": device_id, "destination": {"latitude": 31.232, "longitude": 121.475},
    })
    assert route.status_code == 409


def test_map_without_real_provider_returns_unavailable_instead_of_fake_places(guard_client, monkeypatch):
    client, headers = guard_client
    monkeypatch.setattr(settings, "map_demo_mode", False)
    async def unavailable(*args, **kwargs): return None
    monkeypatch.setattr(remote_guard, "_amap", unavailable)
    report(client, headers)
    device_id = headers["device_id"]
    assert client.get("/api/v1/map/config", headers=headers["guardowner"]).json()["mode"] == "unavailable"
    assert client.post("/api/v1/map/reverse-geocode", headers=headers["guardowner"], json={"device_id":device_id,"latitude":31.23,"longitude":121.47}).status_code == 503
    nearby = client.post("/api/v1/map/nearby", headers=headers["guardowner"], json={"device_id":device_id,"keyword":"医院"})
    assert nearby.status_code == 503
    assert "results" not in nearby.json()
    route = client.post("/api/v1/map/walking-route", headers=headers["guardowner"], json={"device_id":device_id,"destination":{"latitude":31.24,"longitude":121.48}})
    assert route.status_code == 503


def test_location_provider_switch_is_explicit_and_history_is_scoped(guard_client):
    client, headers = guard_client
    device_id = headers["device_id"]
    assert report(client, headers).status_code == 200
    mock_location = {"device_id":device_id,"latitude":31.231,"longitude":121.474,"accuracy":12,"source":"mock","mode":"demo"}
    assert client.post("/api/v1/location/report",headers=headers["guardowner"],json=mock_location).status_code == 409
    switch = client.post("/api/v1/location/provider",headers=headers["guardowner"],json={"device_id":device_id,"source":"mock","enabled":True})
    assert switch.status_code == 200
    owner_devices = client.get("/api/v1/guard/devices", headers=headers["guardowner"]).json()
    family_devices = client.get("/api/v1/guard/devices", headers=headers["guardfamily"]).json()
    assert owner_devices[0]["location_source"] == "mock"
    assert family_devices[0]["location_source"] == "mock"
    assert family_devices[0]["owner"] is False
    assert client.post("/api/v1/location/report",headers=headers["guardowner"],json=mock_location).status_code == 200
    history = client.get(f"/api/v1/location/history?device_id={device_id}",headers=headers["guardowner"]).json()
    assert len(history) == 1 and history[0]["source"] == "mock"


def test_only_device_owner_can_enable_computer_location(guard_client):
    client, headers = guard_client
    payload = {"device_id": headers["device_id"], "source": "browser_geolocation", "enabled": True}
    assert client.post("/api/v1/location/provider", headers=headers["guardfamily"], json=payload).status_code == 403
    assert client.post("/api/v1/location/provider", headers=headers["guardowner"], json=payload).status_code == 200
    devices = client.get("/api/v1/guard/devices", headers=headers["guardowner"]).json()
    assert devices[0]["location_source"] == "browser_geolocation"


def test_runtime_broadcast_forwards_device_event_once(monkeypatch):
    forwarded = []

    async def capture(device_id, event):
        forwarded.append((device_id, event))

    monkeypatch.setattr(remote_guard, "publish_guard_event", capture)
    event = {"type": "detection_update", "device_id": "hat-test", "objects": []}
    asyncio.run(remote_guard.runtime.broadcast(event))
    assert forwarded == [("hat-test", event)]


@pytest.mark.parametrize("path", ["geocode/regeo", "place/around", "place/text", "direction/walking"])
def test_cocomap_uses_verified_rest_path_and_server_only_headers(monkeypatch, path):
    monkeypatch.setattr(settings, "map_service_mode", "cocomap")
    monkeypatch.setattr(settings, "cocomap_api_key", "sk_coco_test_server_only")
    monkeypatch.setattr(settings, "cocomap_base_url", "https://map.culture09.xyz")
    monkeypatch.setattr(settings, "amap_web_key", "must-not-be-forwarded")
    requests = []
    upstream = {"status": "1", "infocode": "10000", "pois": []}
    def handle(request):
        requests.append(request)
        assert request.url.host == "map.culture09.xyz"
        assert request.url.path == "/v3/" + path
        assert request.headers["authorization"] == "Bearer sk_coco_test_server_only"
        assert request.headers["x-cocomap-provider"] == "amap"
        assert "key" not in request.url.params
        assert "callback" not in request.url.params
        assert "sk_coco" not in str(request.url)
        return httpx.Response(200, json={"ok": True, "data": upstream})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(remote_guard.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(remote_guard._amap(path, {"keywords": "医院", "key": "do-not-forward", "callback": "do-not-forward"}))
    assert result == upstream
    assert len(requests) == 1


@pytest.mark.parametrize("status,body", [
    (401, {"ok": False, "error": "INVALID_API_KEY"}),
    (402, {"ok": False, "error": "QUOTA_EXHAUSTED"}),
    (429, {"ok": False, "error": "RATE_LIMITED"}),
    (200, {"ok": False, "data": {"status": "1"}}),
    (200, {"ok": True, "data": {"status": "0", "info": "INVALID_USER_KEY"}}),
    (200, {"status": "1"}),
    (200, {"ok": True, "data": []}),
])
def test_cocomap_failure_does_not_fall_back_to_another_provider(monkeypatch, status, body):
    monkeypatch.setattr(settings, "map_service_mode", "cocomap")
    monkeypatch.setattr(settings, "cocomap_api_key", "sk_coco_test")
    monkeypatch.setattr(settings, "cocomap_base_url", "https://map.culture09.xyz")
    monkeypatch.setattr(settings, "amap_web_key", "unused-official-key")
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=body)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(remote_guard.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    assert asyncio.run(remote_guard._amap("place/around", {})) is None
    assert len(requests) == 1


def test_map_config_never_exposes_gateway_secret(guard_client, monkeypatch):
    client, headers = guard_client
    monkeypatch.setattr(settings, "cocomap_api_key", "sk_coco_secret_not_for_browser")
    monkeypatch.setattr(settings, "amap_js_key", "sk_coco_accidental_wrong_field")
    response = client.get("/api/v1/map/config", headers=headers["guardowner"])
    assert response.status_code == 200
    assert "sk_coco" not in response.text
    assert response.json()["service_mode"] == "cocomap"
    assert response.json()["web_configured"] is True
    assert response.json()["configured"] is False


def test_map_config_selects_baidu_ak_without_exposing_server_secret(guard_client, monkeypatch):
    client, headers = guard_client
    monkeypatch.setattr(settings, "map_js_provider", "baidu")
    monkeypatch.setattr(settings, "baidu_map_js_ak", "browser-baidu-ak")
    monkeypatch.setattr(settings, "cocomap_api_key", "sk_coco_do_not_return")
    response = client.get("/api/v1/map/config", headers=headers["guardowner"])
    assert response.status_code == 200
    assert response.json()["js_provider"] == "baidu"
    assert response.json()["js_key"] == "browser-baidu-ak"
    assert response.json()["configured"] is True
    assert "sk_coco" not in response.text


def test_missing_gateway_key_does_not_use_amap_key(monkeypatch):
    monkeypatch.setattr(settings, "map_service_mode", "cocomap")
    monkeypatch.setattr(settings, "cocomap_api_key", "")
    monkeypatch.setattr(settings, "amap_web_key", "official-key-must-stay-unused")
    def forbidden(**_kwargs):
        pytest.fail("No network request should run without the selected gateway key")
    monkeypatch.setattr(remote_guard.httpx, "AsyncClient", forbidden)
    assert asyncio.run(remote_guard._amap("geocode/regeo", {})) is None


def test_keyword_search_uses_gateway_and_preserves_missing_distance(guard_client, monkeypatch):
    client, headers = guard_client
    monkeypatch.setattr(settings, "cocomap_api_key", "sk_coco_keyword_test")
    seen = []
    def handle(request):
        seen.append(request)
        assert request.url.path == "/v3/place/text"
        assert request.url.params["keywords"] == "目的地名称"
        assert request.url.params["city"] == "上海"
        return httpx.Response(200, json={"ok": True, "data": {"status": "1", "pois": [
            {"name": "API返回的地点", "address": "API返回的地址", "location": "121.478,31.228"},
            {"name": "无坐标记录", "location": "invalid"},
        ]}})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(remote_guard.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    payload = {"device_id": headers["device_id"], "keyword": "目的地名称", "city": "上海"}
    assert client.post("/api/v1/map/search", headers=headers["guardother"], json=payload).status_code == 403
    response = client.post("/api/v1/map/search", headers=headers["guardowner"], json=payload)
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    poi = response.json()["results"][0]
    assert poi["name"] == "API返回的地点"
    assert poi["distance"] is None
    assert poi["longitude"] != 121.478  # normalized from GCJ-02 to WGS-84
    assert len(seen) == 1


def test_keyword_search_without_gateway_returns_unavailable(guard_client):
    client, headers = guard_client
    payload = {"device_id": headers["device_id"], "keyword": "医院"}
    response = client.post("/api/v1/map/search", headers=headers["guardowner"], json=payload)
    assert response.status_code == 503
    assert "results" not in response.json()
    payload["keyword"] = "   "
    assert client.post("/api/v1/map/search", headers=headers["guardowner"], json=payload).status_code == 422


def test_simulated_camera_results_never_reach_guard_family_websocket(guard_client):
    class Socket:
        def __init__(self): self.events = []
        async def send_json(self, event): self.events.append(event)

    _client, headers = guard_client
    device_id = headers["device_id"]
    socket = Socket()
    token = headers["guardfamily"]["Authorization"].split(" ", 1)[1]
    remote_guard._guard_clients[device_id] = {socket}
    remote_guard._guard_sessions[socket] = ("guardfamily", device_id, False, token)
    asyncio.run(remote_guard.publish_guard_event(device_id, {
        "type": "detection_update", "simulation": True, "objects": [{"class": "person"}],
    }))
    assert socket.events == []
    real = {"type": "detection_update", "model_status": "ready", "objects": []}
    asyncio.run(remote_guard.publish_guard_event(device_id, real))
    assert socket.events == [real]
