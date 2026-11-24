import asyncio
from collections import OrderedDict

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import auth, database
from backend.camera_stream import camera_frames
from backend.config import settings
from backend.main import app
from backend.services import runtime


@pytest.fixture
def tablet_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'tablet.sqlite3'))
    monkeypatch.setattr(runtime, 'image_queue', asyncio.Queue(maxsize=1))
    monkeypatch.setattr(runtime, 'devices', {})
    monkeypatch.setattr(runtime, 'web_clients', set())
    monkeypatch.setattr(runtime, 'speech_enabled', True)
    monkeypatch.setattr(runtime, 'motor_monitor_leases', {})
    monkeypatch.setattr(runtime, 'locations', {})
    monkeypatch.setattr(camera_frames, '_frames', OrderedDict())
    monkeypatch.setattr(camera_frames, '_clock', lambda: 100.0)
    database.init_db()
    auth.register_user('tabletuser', 'tablet@example.com', 'Tablet123')
    token = auth.create_session('tabletuser', 'Tablet123', False)['token']
    # No lifespan/model loading needed for transport tests.
    client = TestClient(app)
    try:
        yield client, {'Authorization': 'Bearer ' + token}
    finally:
        client.close()


def test_frame_auth_validation_and_separate_source(tablet_client):
    client, headers = tablet_client
    url = '/api/tablet/frame?source_id=tablet_0123456789abcdef&seq=3'
    jpeg = b'\xff\xd8test\xff\xd9'
    assert client.post(url, content=jpeg).status_code == 401
    assert client.post(url.replace('tablet_0123456789abcdef', 'local_camera'), content=jpeg, headers=headers).status_code == 422
    assert client.post(url, content=b'bad', headers=headers).status_code == 400
    assert client.post(url, content=b'x' * 2_000_000, headers=headers).status_code == 413
    response = client.post(url, content=jpeg, headers=headers)
    assert response.json()['device_id'] == 'tablet_0123456789abcdef'
    assert runtime.image_queue.get_nowait() == ('tablet_0123456789abcdef', 3, jpeg)


def test_device_status_uses_current_connections(tablet_client):
    client, headers = tablet_client
    with database.db() as c:
        c.execute("INSERT INTO device_info(device_id,online) VALUES('stale_hat',1)")
        c.execute("INSERT INTO device_info(device_id,online) VALUES('simulator_001',0)")
    runtime.devices['simulator_001'] = object()
    assert client.get('/api/tablet/devices').status_code == 401
    devices = {d['device_id']: d for d in client.get('/api/tablet/devices', headers=headers).json()}
    assert devices['stale_hat']['online'] is False
    assert devices['simulator_001']['online'] is True
    assert devices['simulator_001']['simulation'] is True


def test_location_endpoint_requires_hat_device_and_authentication(tablet_client):
    client, headers = tablet_client
    runtime.locations['esp32s3_cam_001'] = {'available': False, 'status': 'no_match'}
    path = '/api/tablet/location?device_id=esp32s3_cam_001'
    assert client.get(path).status_code == 401
    assert client.get('/api/tablet/location?device_id=local_camera', headers=headers).status_code == 422
    assert client.get(path, headers=headers).json()['status'] == 'no_match'
    assert client.get('/api/tablet/location?device_id=esp32s3_cam_001&other=x', headers=headers).status_code == 200


def test_tablet_config_auth_and_exposed_settings(tablet_client):
    client, headers = tablet_client
    assert client.get('/api/tablet/config').status_code == 401
    assert client.put('/api/tablet/config', json={'confidence': .5}).status_code == 401
    assert client.put('/api/tablet/config', json={'speech_enabled': True}, headers=headers).status_code == 422
    assert client.get('/api/tablet/config', headers=headers).status_code == 200


def test_hardware_speech_switch_updates_runtime_and_persists(tablet_client):
    client, _ = tablet_client
    response = client.put('/api/config', json={'speech_enabled': False})
    assert response.status_code == 200
    assert response.json()['speech_enabled'] is False
    assert runtime.speech_enabled is False
    assert client.get('/api/config').json()['speech_enabled'] is False
    assert client.put('/api/config', json={'speech_enabled': 'false'}).status_code == 422


def test_configuration_accepts_full_confidence_range_and_validates_cooldown(tablet_client, monkeypatch):
    client, _ = tablet_client
    initial_confidence = settings.yolo_confidence
    monkeypatch.setattr(settings, 'yolo_confidence', initial_confidence)

    assert client.put('/api/config', json={
        'confidence': 0, 'alert_cooldown_ms': 1}).status_code == 200
    assert client.put('/api/config', json={
        'confidence': 1, 'alert_cooldown_ms': 2000}).status_code == 200
    assert client.put('/api/config', json={'confidence': 1.01}).status_code == 422
    assert client.put('/api/config', json={'confidence': -0.01}).status_code == 422
    assert client.put('/api/config', json={'alert_cooldown_ms': 1.5}).status_code == 422
    assert client.put('/api/config', json={'alert_cooldown_ms': 0}).status_code == 422


def test_motor_monitor_requires_auth_and_only_arms_hat_camera(tablet_client):
    client, headers = tablet_client
    assert client.post('/api/tablet/motor-monitor', json={
        'active': True, 'device_id': 'esp32s3_cam_001'}).status_code == 401
    assert client.post('/api/tablet/motor-monitor', json={
        'active': True, 'device_id': 'local_camera'}, headers=headers).status_code == 422
    armed = client.post('/api/tablet/motor-monitor', json={
        'active': True, 'device_id': 'esp32s3_cam_001'}, headers=headers)
    assert armed.status_code == 200
    assert armed.json()['active'] is True
    username = auth.session_username(headers['Authorization'].split(' ', 1)[1])
    assert runtime.motor_monitor_enabled('esp32s3_cam_001')
    stopped = client.post('/api/tablet/motor-monitor', json={
        'active': False, 'device_id': 'esp32s3_cam_001'}, headers=headers)
    assert stopped.status_code == 200
    assert not runtime.motor_monitor_enabled('esp32s3_cam_001')
    assert username not in runtime.motor_monitor_leases


def test_socket_auth_ping_and_revocation(tablet_client):
    client, headers = tablet_client
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/tablet'):
            pass
    with client.websocket_connect('/ws/tablet', headers=headers) as ws:
        ws.send_json({'type': 'ping'})
        assert ws.receive_json() == {'type': 'pong'}
        assert client.post('/api/auth/logout', headers=headers).status_code == 200
        ws.send_json({'type': 'ping'})
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 4401
    assert not runtime.web_clients


def test_camera_preview_requires_auth_and_forwards_web_jpeg(tablet_client):
    client, headers = tablet_client
    url = '/api/tablet/cameras/local_camera/frame'
    assert client.get('/api/tablet/cameras').status_code == 401
    assert client.get(url).status_code == 401
    sources = client.get('/api/tablet/cameras', headers=headers).json()
    assert sources == [{'source_id': 'local_camera', 'label': '电脑网页摄像头',
                        'kind': 'web', 'online': False, 'seq': None}]
    assert client.get(url, headers=headers).status_code == 410
    jpeg = b'\xff\xd8web-camera-frame\xff\xd9'
    assert client.post('/api/local-camera/frame?seq=17', content=jpeg).status_code == 200
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.content == jpeg
    assert response.headers['content-type'] == 'image/jpeg'
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['x-frame-seq'] == '17'
    source = client.get('/api/tablet/cameras', headers=headers).json()[0]
    assert source['online'] is True
    assert source['seq'] == 17


def test_preview_does_not_expose_stale_or_previous_source(tablet_client, monkeypatch):
    client, headers = tablet_client
    tablet_id = 'tablet_0123456789abcdef'
    first = b'\xff\xd8first-web-frame\xff\xd9'
    second = b'\xff\xd8second-web-frame\xff\xd9'
    tablet = b'\xff\xd8tablet-frame\xff\xd9'
    camera_frames.publish('local_camera', 1, first)
    camera_frames.publish(tablet_id, 2, tablet)
    camera_frames.publish('local_camera', 3, second)
    base = '/api/tablet/cameras/'
    assert client.get(base + 'local_camera/frame', headers=headers).content == second
    assert client.get(base + tablet_id + '/frame', headers=headers).content == tablet
    assert client.get(base + 'unknown_camera/frame', headers=headers).status_code == 410
    monkeypatch.setattr(camera_frames, '_clock', lambda: 103.1)
    assert client.get(base + 'local_camera/frame', headers=headers).status_code == 410
    assert client.get('/api/tablet/cameras', headers=headers).json()[0]['online'] is False


def test_tablet_upload_can_be_viewed_without_reuploading(tablet_client):
    client, headers = tablet_client
    source = 'tablet_0123456789abcdef'
    jpeg = b'\xff\xd8real-tablet-upload\xff\xd9'
    response = client.post('/api/tablet/frame?source_id=' + source + '&seq=9',
                           content=jpeg, headers=headers)
    assert response.status_code == 200
    assert client.get('/api/tablet/cameras/' + source + '/frame', headers=headers).content == jpeg
    assert camera_frames.get(source).seq == 9
