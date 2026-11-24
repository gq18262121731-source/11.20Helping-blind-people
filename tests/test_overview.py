import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.overview as dashboard
from backend.config import settings
from backend.database import db, init_db


@pytest.fixture
def overview_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'overview.sqlite3'))
    monkeypatch.setattr(dashboard, '_probe_cache', {})
    monkeypatch.setattr(dashboard, '_probe_lock', asyncio.Lock())
    init_db()


def alert(connection, device_id, timestamp):
    connection.execute(
        'INSERT INTO obstacle_logs(device_id,detected_at,object_class,direction,confidence,voice_text,created_at) VALUES(?,?,?,?,?,?,?)',
        (device_id, timestamp, 'person', 'front', .9, 'test', timestamp),
    )


def voice(connection, device_id, timestamp, asr='ready', tts='ready'):
    connection.execute(
        'INSERT INTO voice_logs(device_id,occurred_at,user_text,reply_text,request_type,asr_status,tts_status,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (device_id, timestamp, 'question', 'reply', 'dialogue', asr, tts, timestamp),
    )


def test_statistics_use_local_midnight_all_records_and_separate_simulation(overview_db):
    with db() as connection:
        for _ in range(130):
            alert(connection, 'local_camera', '2026-11-17T01:00:00+00:00')
        for _ in range(3):
            alert(connection, 'simulator_001', '2026-11-17T02:00:00+00:00')
        alert(connection, 'simulator_001', '2026-11-16T16:00:00+00:00')
        alert(connection, 'hat_001', '2026-11-16T15:59:59+00:00')
        alert(connection, 'hat_001', '2026-11-10T01:00:00+00:00')
        voice(connection, 'hat_001', '2026-11-17T03:00:00+08:00')
        voice(connection, 'local_camera', '2026-11-17T04:00:00+08:00', asr='simulation')
        voice(connection, 'simulator_001', '2026-11-17T05:00:00+08:00')
    data = dashboard.dashboard_statistics('Asia/Shanghai', datetime(2026, 11, 17, 8, tzinfo=timezone.utc))
    assert len(data['trend']) == 7
    assert data['trend'][0]['date'] == '2026-11-11'
    assert data['today_alerts']['total'] == 134
    assert data['today_alerts']['real'] == 130
    assert data['today_alerts']['simulation'] == 4
    assert data['trend'][-2]['real'] == 1
    assert data['today_voices'] == {'total': 3, 'real': 1, 'simulation': 2}
    assert len(data['recent_alerts']) == 5
    assert data['recent_alerts'][0]['source'] == 'simulation'
    assert len(data['recent_voices']) == 3


def test_empty_statistics_and_daylight_saving_boundary(overview_db):
    data = dashboard.dashboard_statistics('America/New_York', datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    assert data['today_alerts']['total'] == 0
    assert data['recent_alerts'] == []
    assert all(point['real'] == point['simulation'] == 0 for point in data['trend'])
    with db() as connection:
        alert(connection, 'hat_001', '2026-11-01T04:30:00+00:00')
        alert(connection, 'hat_001', '2026-11-02T04:30:00+00:00')
    data = dashboard.dashboard_statistics('America/New_York', datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    assert data['trend'][-2]['real'] == 2  # Nov 1 has 25 hours in this timezone.
    assert data['today_alerts']['total'] == 0
    with pytest.raises(ValueError):
        dashboard.dashboard_statistics('not/a-timezone')


def test_brand_styles_and_hat_outline_asset_have_live_routes():
    from backend import main

    client = TestClient(main.app)
    for path in ("/brand.css", "/login-theme.css", "/overview-theme.css", "/login.js", "/assets/qiming-hat-outline.png"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content
    assert client.get("/brand.css").headers["content-type"].startswith("text/css")
    assert client.get("/login.js").headers["cache-control"] == "no-store"
    assert client.get("/assets/qiming-hat-outline.png").headers["content-type"] == "image/png"


@pytest.mark.parametrize('scenario, expected', [('ready', 'available'), ('missing', 'missing'), ('offline', 'offline'), ('denied', 'error'), ('malformed', 'error')])
def test_model_status_probe(monkeypatch, scenario, expected):
    monkeypatch.setattr(settings, 'openai_base_url', 'http://model.test/v1')
    monkeypatch.setattr(settings, 'openai_model', 'qwen3-vl:4b')
    original_client = httpx.AsyncClient

    def handler(request):
        assert request.url.path == '/v1/models'
        if scenario == 'offline':
            raise httpx.ConnectError('offline', request=request)
        if scenario == 'denied':
            return httpx.Response(401)
        if scenario == 'malformed':
            return httpx.Response(200, json={'unexpected': []})
        return httpx.Response(200, json={'data': [{'id': 'qwen3-vl:4b'}] if scenario == 'ready' else []})

    monkeypatch.setattr(dashboard.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(dashboard._probe_model())['state'] == expected


def test_model_checks_are_cached_and_config_changes_invalidate_cache(overview_db, monkeypatch):
    calls = []

    async def probe():
        calls.append(1)
        await asyncio.sleep(0)
        return {'state': 'available'}

    monkeypatch.setattr(dashboard, '_probe_model', probe)

    async def check():
        await asyncio.gather(dashboard.model_status(), dashboard.model_status())
        assert len(calls) == 1
        monkeypatch.setattr(settings, 'openai_model', 'another-model')
        await dashboard.model_status()
        assert len(calls) == 2

    asyncio.run(check())


def test_overview_uses_current_connections_instead_of_stale_database_flags(overview_db, monkeypatch):
    with db() as connection:
        connection.execute("INSERT INTO device_info(device_id,online) VALUES('hat_001',1)")
    monkeypatch.setattr(dashboard.runtime, 'devices', {'simulator_001': object()})
    monkeypatch.setattr(dashboard.runtime, 'model', object())
    monkeypatch.setattr(settings, 'simulation_mode', True)

    async def probe():
        return {'state': 'offline', 'label': '服务未连接', 'detail': 'test'}

    monkeypatch.setattr(dashboard, 'model_status', probe)
    data = asyncio.run(dashboard.overview('Asia/Shanghai'))
    assert data['devices'] == {'real_online': 0, 'simulation_online': 1, 'seen': 1}
    assert data['mode'] == 'simulation'
    assert data['yolo']['state'] == 'ready'
    assert data['llm']['state'] == 'offline'


def test_overview_endpoint_requires_login_and_valid_timezone(overview_db, monkeypatch):
    from backend.auth import create_session, register_user
    from backend.main import app

    async def probe():
        return {'state': 'available', 'label': '模型已安装', 'detail': 'test'}

    monkeypatch.setattr(dashboard, 'model_status', probe)
    register_user('overviewuser', 'overview@example.com', 'Test123')
    token = create_session('overviewuser', 'Test123', False)['token']
    client = TestClient(app)
    assert client.get('/api/overview').status_code == 401
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/api/overview?tz=bad-zone', headers=headers).status_code == 422
    response = client.get('/api/overview?tz=Asia%2FShanghai', headers=headers)
    assert response.status_code == 200
    assert len(response.json()['trend']) == 7
    assert client.get('/overview.js').status_code == 200
    assert client.get('/overview.css').status_code == 200
