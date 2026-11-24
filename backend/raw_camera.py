"""Transport for QVGA grayscale pixels and software-compressed camera JPEGs."""
import asyncio
import io
import json
import logging
import time

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .camera_stream import SOURCE_ID, camera_frames
from .database import db, now_iso
from .geolocation import GeolocationError, locate_wifi
from .services import runtime
from .tts_delivery import tts_delivery

router = APIRouter()
logger = logging.getLogger(__name__)
WIDTH, HEIGHT = 320, 240
FRAME_BYTES = WIDTH * HEIGHT
MAX_JPEG_BYTES = 256_000
DEFAULT_DEVICE_ID = "esp32s3_cam_001"


async def process_wifi_scan(device_id, connection, access_points):
    try:
        location = await locate_wifi(access_points)
    except GeolocationError as error:
        location = {"available": False, "status": error.status, "detail": str(error),
                    "access_points_used": min(len(access_points), 20), "updated_at": now_iso()}
    except Exception:
        logger.exception("Unexpected Wi-Fi location failure for %s", device_id)
        location = {"available": False, "status": "provider_unavailable",
                    "detail": "定位服务暂不可用", "updated_at": now_iso()}
    if runtime.devices.get(device_id) is connection:
        runtime.locations[device_id] = location
        await runtime.broadcast({"type": "location_update", "device_id": device_id,
                                 "location": location})


def grayscale_jpeg(raw: bytes) -> bytes:
    """Raw pixels are not JPEG: validate before reshaping or decoding."""
    if len(raw) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} grayscale bytes, received {len(raw)}")
    image = np.frombuffer(raw, dtype=np.uint8).reshape(HEIGHT, WIDTH)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("grayscale JPEG encoding failed")
    return jpeg.tobytes()


def validated_camera_jpeg(data: bytes) -> bytes:
    """Validate dimensions before decoding; keep the device's compressed bytes."""
    if not 0 < len(data) <= MAX_JPEG_BYTES or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("expected a complete QVGA JPEG within 256000 bytes")
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != "JPEG" or picture.size != (WIDTH, HEIGHT):
                raise ValueError("expected a 320x240 JPEG")
            picture.verify()
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != (HEIGHT, WIDTH):
            raise ValueError("invalid JPEG pixels")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, cv2.error) as error:
        raise ValueError("invalid QVGA JPEG") from error
    return data


class RawCameraConnection:
    """Adapt the runtime's JSON commands without putting inference in receive()."""

    def __init__(self, ws, protocol=1, device_id=DEFAULT_DEVICE_ID):
        self.ws = ws
        self.protocol = protocol
        self.device_id = device_id
        self._send_lock = asyncio.Lock()

    def _queue_speech(self, text, priority=1):
        result = tts_delivery.queue_text(self.device_id, text, priority=priority)
        if result.get("status") not in ("queued", "synthesizing"):
            logger.info("Hat TTS phrase not queued for %s: %s", self.device_id, result.get("status"))
            return False
        return True

    def speak_text(self, text):
        if self.protocol != 2 or not runtime.speech_enabled:
            return False
        return self._queue_speech(text, priority=2)

    def queue_family_message(self, text):
        """Reserve every text chunk before accepting a remote announcement."""
        if self.protocol != 2 or not runtime.speech_enabled or not isinstance(text, str):
            return False
        text = text.strip()
        if not text or len(text) > 500:
            return False
        return self._queue_speech(text, priority=1)

    async def _send(self, *, commands=(), text=None):
        async def write():
            async with self._send_lock:
                for command in commands:
                    await self.ws.send_bytes(command)
                if text is not None:
                    await self.ws.send_text(text)
        # A slow/offline camera must not indefinitely hold the detector loop.
        await asyncio.wait_for(write(), timeout=2.0)

    async def send_json(self, message):
        commands = []
        if message.get("type") == "obstacle_alert":
            direction = message.get("vibration") if runtime.motor_monitor_enabled(message.get("device_id")) else None
        elif message.get("type") == "device_command" and message.get("command") == "vibrate":
            direction = message.get("direction")
        elif message.get("type") == "device_command" and message.get("command") == "stop":
            direction = "stop"
        elif message.get("type") == "family_message":
            self.queue_family_message(message.get("body", ""))
            return
        else:
            return
        commands.extend({"left": (b"L",), "right": (b"R",)}.get(direction, ()))
        if direction == "both":
            # The original sketch only understands L/R; v2 adds simultaneous B.
            commands.extend((b"B",) if self.protocol == 2 else (b"L", b"R"))
        elif direction == "stop" and self.protocol == 2:
            commands.append(b"S")
        if commands:
            await self._send(commands=commands)
        if (message.get("type") == "obstacle_alert" and message.get("voice_text")
                and runtime.speech_enabled):
            self._queue_speech(message["voice_text"], priority=0)

    async def text(self, message):
        await self._send(text=message)

    async def close(self):
        await asyncio.wait_for(self.ws.close(code=1012), timeout=2.0)


@router.websocket("/ws/camera")
async def raw_camera_socket(ws: WebSocket):
    device_id = ws.query_params.get("device_id", DEFAULT_DEVICE_ID)
    version = ws.query_params.get("protocol", "1")
    frame_format = ws.query_params.get("format", "gray")
    if (not SOURCE_ID.fullmatch(device_id) or
            device_id == "local_camera" or device_id.startswith(("tablet_", "simulator_")) or
            version not in ("1", "2") or frame_format not in ("gray", "jpeg")):
        await ws.close(code=1008)
        return
    previous = runtime.devices.get(device_id)
    if previous is not None and not isinstance(previous, RawCameraConnection):
        await ws.close(code=1008)
        return
    await ws.accept()
    connection = RawCameraConnection(ws, int(version), device_id=device_id)
    runtime.devices[device_id] = connection
    runtime.speech_announced.pop(device_id, None)
    seq = 0
    last_status_write = time.monotonic()
    location_tasks = set()
    try:
        if previous is not None:
            try:
                await previous.close()
            except (WebSocketDisconnect, OSError, RuntimeError, asyncio.TimeoutError):
                pass
        with db() as c:
            c.execute(
                "INSERT INTO device_info(device_id,name,firmware_version,online,last_heartbeat,"
                "camera_ok,mic_ok,left_motor_ok,right_motor_ok,speaker_ok,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
                "firmware_version=excluded.firmware_version,online=1,last_heartbeat=excluded.last_heartbeat,"
                "camera_ok=NULL,mic_ok=NULL,left_motor_ok=NULL,right_motor_ok=NULL,speaker_ok=NULL,"
                "updated_at=excluded.updated_at",
                (device_id, "ESP32-S3 灰度摄像头", f"gray-ws-v{version}-{frame_format}", 1, now_iso(),
                 None, None, None, None, None, now_iso()),
            )
        await runtime.broadcast({"type": "device_status", "device_id": device_id,
                                 "online": True, "timestamp": now_iso()})
        logger.info("Raw camera connected: %s protocol=%s format=%s", device_id, version, frame_format)
        while runtime.devices.get(device_id) is connection:
            # Allow slow Wi-Fi without treating an indefinitely idle socket as healthy.
            data = await asyncio.wait_for(ws.receive(), timeout=30.0)
            if data["type"] == "websocket.disconnect":
                break
            raw = data.get("bytes")
            if raw is None:
                try:
                    message = data.get("text", "")
                    if len(message) > 4096:
                        await connection.text("ERR:BAD_WIFI_SCAN")
                        continue
                    payload = json.loads(message)
                except (TypeError, ValueError):
                    await connection.text("ERR:EXPECTED_BINARY_QVGA_OR_WIFI_SCAN")
                    continue
                access_points = payload.get("access_points") if isinstance(payload, dict) else None
                if (not isinstance(payload, dict) or payload.get("type") != "wifi_scan"
                        or not isinstance(access_points, list) or len(access_points) > 20):
                    await connection.text("ERR:BAD_WIFI_SCAN")
                    continue
                pending = {"available": False, "status": "locating",
                           "access_points_used": len(access_points), "updated_at": now_iso()}
                runtime.locations[device_id] = pending
                await runtime.broadcast({"type": "location_update", "device_id": device_id,
                                         "location": pending})
                task = asyncio.create_task(process_wifi_scan(device_id, connection, access_points))
                location_tasks.add(task)
                task.add_done_callback(location_tasks.discard)
                await connection.text("WIFI_SCAN_RECEIVED")
                continue
            try:
                convert = validated_camera_jpeg if frame_format == "jpeg" else grayscale_jpeg
                jpeg = await asyncio.to_thread(convert, raw)
            except ValueError as error:
                await connection.text(f"ERR:BAD_FRAME:{error}")
                continue
            # A reconnect may have superseded us while the conversion ran.
            if runtime.devices.get(device_id) is not connection:
                break
            seq += 1
            camera_frames.publish(device_id, seq, jpeg)
            runtime.enqueue_frame(device_id, seq, jpeg)
            if connection.protocol == 2:
                # Receipt/queue ACK only; preview must not wait for inference or SQLite.
                await connection.text("ACK")
            if seq == 1 or time.monotonic() - last_status_write >= 10:
                with db() as c:
                    c.execute("UPDATE device_info SET camera_ok=1,last_heartbeat=?,updated_at=? "
                              "WHERE device_id=?", (now_iso(), now_iso(), device_id))
                last_status_write = time.monotonic()
    except (WebSocketDisconnect, OSError, RuntimeError, asyncio.TimeoutError) as error:
        logger.info("Raw camera ended: %s (%s)", device_id, type(error).__name__)
    finally:
        for task in location_tasks:
            task.cancel()
        # The old socket's finally must never mark its replacement offline.
        if runtime.devices.get(device_id) in (None, connection):
            runtime.devices.pop(device_id, None)
            runtime.speech_announced.pop(device_id, None)
            camera_frames.remove(device_id)
            with db() as c:
                c.execute("UPDATE device_info SET online=0,camera_ok=NULL,updated_at=? WHERE device_id=?",
                          (now_iso(), device_id))
            await runtime.broadcast({"type": "device_status", "device_id": device_id,
                                     "online": False, "timestamp": now_iso()})
        try:
            await connection.close()
        except (WebSocketDisconnect, OSError, RuntimeError, asyncio.TimeoutError):
            pass

