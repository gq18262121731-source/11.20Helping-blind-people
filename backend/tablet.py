"""Authenticated tablet transport; legacy web and firmware protocols stay intact."""
import asyncio
import re

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from .auth import require_session
from .camera_stream import SOURCE_ID, camera_frames
from .database import db
from .services import runtime

router = APIRouter()
MAX_FRAME_BYTES = 2_000_000


@router.post('/api/tablet/frame')
async def tablet_frame(request: Request, source_id: str, seq: int = 0,
                       authorization: str | None = Header(default=None)):
    require_session(authorization)
    if not re.fullmatch(r'tablet_[a-f0-9]{16,32}', source_id) or seq < 0:
        raise HTTPException(status_code=422, detail='无效的平板图像来源或序号')
    image = bytearray()
    async for chunk in request.stream():
        image.extend(chunk)
        if len(image) >= MAX_FRAME_BYTES:
            raise HTTPException(status_code=413, detail='检测图像需要小于 2 MB')
    if not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
        raise HTTPException(status_code=400, detail='需要完整的 JPEG 图像')
    camera_frames.publish(source_id, seq, image)
    runtime.enqueue_frame(source_id, seq, bytes(image))
    return {'accepted': True, 'device_id': source_id, 'seq': seq}


@router.get('/api/tablet/cameras')
async def tablet_cameras(authorization: str | None = Header(default=None)):
    require_session(authorization)
    return camera_frames.sources(list(runtime.devices))


@router.get('/api/tablet/cameras/{source_id}/frame')
async def tablet_camera_frame(source_id: str,
                              authorization: str | None = Header(default=None)):
    require_session(authorization)
    if not SOURCE_ID.fullmatch(source_id):
        raise HTTPException(status_code=422, detail='无效的摄像头来源')
    frame = camera_frames.get(source_id)
    if frame is None:
        # A stopped camera must not appear to be a live, frozen scene.
        raise HTTPException(status_code=410, detail='摄像头未开启或已停止上传画面')
    return Response(frame.image, media_type='image/jpeg', headers={
        'Cache-Control': 'no-store, private',
        'X-Frame-Seq': str(frame.seq),
        'X-Content-Type-Options': 'nosniff',
    })


@router.post('/api/tablet/motor-monitor')
async def tablet_motor_monitor(payload: dict,
                               authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    active = payload.get('active')
    device_id = payload.get('device_id')
    if not isinstance(active, bool):
        raise HTTPException(status_code=422, detail='active 必须是布尔值')
    if active and (not isinstance(device_id, str) or not SOURCE_ID.fullmatch(device_id)
                   or device_id in ('local_camera',) or device_id.startswith(('tablet_', 'simulator_'))):
        raise HTTPException(status_code=422, detail='只能为已选择的帽端摄像头启用马达告警')
    await runtime.set_motor_monitor(username, device_id, active)
    return {'active': active, 'device_id': device_id if active else None, 'lease_seconds': 15 if active else 0}


@router.get('/api/tablet/devices')
async def tablet_devices(authorization: str | None = Header(default=None)):
    require_session(authorization)
    with db() as connection:
        rows = [dict(row) for row in connection.execute(
            'SELECT * FROM device_info ORDER BY updated_at DESC').fetchall()]
    # Saved peripheral flags are not hardware self-test results.
    for row in rows:
        row['online'] = row['device_id'] in runtime.devices
        row['simulation'] = row['device_id'].lower().startswith('simulator_')
    return rows


@router.get('/api/tablet/location')
async def tablet_location(device_id: str, authorization: str | None = Header(default=None)):
    require_session(authorization)
    if (not SOURCE_ID.fullmatch(device_id) or device_id == 'local_camera'
            or device_id.startswith(('tablet_', 'simulator_'))):
        raise HTTPException(status_code=422, detail='无效的帽端设备编号')
    return runtime.locations.get(device_id, {
        'available': False, 'status': 'awaiting_scan', 'detail': '等待 ESP32 上传 Wi-Fi 扫描结果'
    })


@router.websocket('/ws/tablet')
async def tablet_socket(ws: WebSocket):
    authorization = ws.headers.get('authorization')
    try:
        require_session(authorization)
    except HTTPException:
        await ws.close(code=4401)
        return
    await ws.accept()
    runtime.web_clients.add(ws)
    try:
        while True:
            # Validate idle sessions too, so revoked tokens cannot listen indefinitely.
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=60)
            except asyncio.TimeoutError:
                message = {}
            try:
                require_session(authorization)
            except HTTPException:
                await ws.close(code=4401)
                return
            if message.get('type') == 'ping':
                await ws.send_json({'type': 'pong'})
    except (WebSocketDisconnect, ValueError, RuntimeError):
        pass
    finally:
        runtime.web_clients.discard(ws)
