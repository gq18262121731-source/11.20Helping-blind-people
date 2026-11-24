"""Authenticated family-assistance APIs for the first remote-support release."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import io
import logging
from time import monotonic
import wave

from fastapi import APIRouter, Header, HTTPException, Request, Response

from .asr import asr
from .auth import require_session
from .camera_stream import SOURCE_ID, camera_frames
from .database import db, now_iso
from .services import runtime
from .tts_delivery import tts_delivery
from .vad import VoiceActivityDetector

router = APIRouter()
logger = logging.getLogger(__name__)
_frame_log_times: OrderedDict[tuple[str, str], float] = OrderedDict()
FRAME_LOG_INTERVAL_SECONDS = 30.0
MAX_VOICE_BYTES = 1_000_000
MIN_VOICE_SECONDS = 0.2
MAX_VOICE_SECONDS = 30
VOICE_UPLOAD_TIMEOUT_SECONDS = 20.0
VOICE_TRANSCRIBE_TIMEOUT_SECONDS = 60.0
_voice_lock = asyncio.Lock()


def _device(value: object) -> str:
    device_id = str(value or "")
    if (not SOURCE_ID.fullmatch(device_id) or device_id == "local_camera"
            or device_id.startswith(("tablet_", "simulator_"))):
        raise HTTPException(status_code=422, detail="无效的帽端设备编号")
    return device_id


def _binding(username: str, device_id: str, *, write: bool = False):
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM device_family_bindings WHERE device_id=? "
            "AND (owner_username=? COLLATE NOCASE OR family_username=? COLLATE NOCASE) "
            "ORDER BY (owner_username=family_username COLLATE NOCASE) DESC,id LIMIT 1",
            (device_id, username, username),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="当前账号没有此设备的家属授权")
    if write and row["can_send_messages"] != 1:
        raise HTTPException(status_code=403, detail="当前授权不允许发送远程消息")
    return row


def _log(username: str, device_id: str, action: str):
    with db() as connection:
        connection.execute(
            "INSERT INTO family_access_logs(device_id,actor_username,action,created_at) VALUES(?,?,?,?)",
            (device_id, username, action, now_iso()),
        )


def _log_camera_view(username: str, device_id: str):
    # Continuous previews poll several times per second. Keep an audit entry
    # for each viewing interval without making every JPEG a SQLite write.
    key = (username.casefold(), device_id)
    now = monotonic()
    previous = _frame_log_times.get(key)
    if previous is not None and now - previous < FRAME_LOG_INTERVAL_SECONDS:
        return
    _log(username, device_id, "view_camera_frame")
    _frame_log_times[key] = now
    _frame_log_times.move_to_end(key)
    while len(_frame_log_times) > 2048:
        _frame_log_times.popitem(last=False)


def _speech_status(device_id: str, *, authorized: bool = True):
    if not authorized:
        status, detail = "not_authorized", "当前授权不允许远程播报"
    elif device_id not in tts_delivery.peers:
        status, detail = "offline", "帽端离线，连接设备后可使用远程播报"
    elif getattr(tts_delivery.peers[device_id], "output_ready", None) is False:
        status, detail = "output_unconfigured", "帽端 I2S 输出尚未配置"
    elif not runtime.speech_enabled:
        status, detail = "speech_disabled", "帽端语音播报已关闭，请先开启播报开关"
    else:
        status, detail = "ready", "可优先播放本地缓存，未收录语句由 Edge TTS 合成后播报"
    return {"available": status == "ready", "status": status,
            "detail": detail, "mode": "tts"}


def _deliver_message(device_id: str, text: str):
    speech = _speech_status(device_id)
    if not speech["available"]:
        return {"status": speech["status"],
                "detail": "消息已保存，未加入播报队列。" + speech["detail"]}
    result = tts_delivery.queue_text(device_id, text, priority=1)
    if result.get("status") == "queued":
        return {"status": "queued", "detail": "已加入帽端播报队列，实际播放状态需在帽端确认"}
    if result.get("status") == "synthesizing":
        return {"status": "synthesizing", "detail": "正在合成语音，完成后会自动发送到帽端"}
    if result.get("status") == "synthesis_unavailable":
        return {"status": "synthesis_unavailable", "detail": "Edge TTS 或 FFmpeg 未配置，消息已保存但暂未播报"}
    if result.get("status") == "output_unconfigured":
        return {"status": "output_unconfigured", "detail": "帽端 I2S 输出尚未配置，消息已保存但暂未播报"}
    if result.get("status") == "text_too_long":
        return {"status": "text_too_long", "detail": "消息过长，消息已保存但无法合成为一段语音"}
    if result.get("status") == "device_offline":
        return {"status": "tts_channel_offline", "detail": "帽端音频播放通道未连接，消息已保存"}
    return {"status": "queue_full", "detail": "帽端播报队列已满，消息已保存，请稍后重试"}


async def _read_voice_upload(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_VOICE_BYTES:
            raise HTTPException(status_code=413, detail="录音文件过大，请录制不超过 30 秒的语音")
        data.extend(chunk)
    return bytes(data)


def _validate_voice_wav(data: bytes):
    try:
        with wave.open(io.BytesIO(data), "rb") as recording:
            if (recording.getnchannels() != 1 or recording.getsampwidth() != 2 or
                    recording.getframerate() != 16_000 or recording.getcomptype() != "NONE"):
                raise HTTPException(status_code=400, detail="录音须为 16kHz、16bit、单声道 PCM WAV")
            frames = recording.getnframes()
            if frames > MAX_VOICE_SECONDS * 16_000:
                raise HTTPException(status_code=413, detail="录音超过 30 秒，请缩短后重试")
            if frames < MIN_VOICE_SECONDS * 16_000:
                raise HTTPException(status_code=400, detail="录音太短，请至少录制 0.2 秒后重试")
            pcm = recording.readframes(frames)
            if frames == 0 or len(pcm) != frames * 2:
                raise HTTPException(status_code=400, detail="录音为空或数据不完整，请重新录音")
    except (wave.Error, EOFError, OSError) as error:
        raise HTTPException(status_code=400, detail="录音文件损坏或不是有效的 WAV 文件，请重新录音") from error
    detector = VoiceActivityDetector(threshold=100, min_speech_ms=120)
    if not any(detector.is_speech(pcm[offset:offset + 640])
               for offset in range(0, len(pcm), 640)):
        raise HTTPException(status_code=400, detail="未检测到有效语音，请靠近麦克风重新录音")


def _finish_voice_task(task: asyncio.Task, gate: asyncio.Lock):
    # A timed-out to_thread inference continues in its worker. Retain the slot
    # until it actually finishes so retries cannot create unbounded inference.
    if not task.cancelled():
        task.exception()
    gate.release()


@router.post("/api/family/voice/transcribe")
async def transcribe_family_voice(request: Request, device_id: str,
                                  authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(device_id)
    _binding(username, device_id, write=True)
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "audio/wav":
        raise HTTPException(status_code=415, detail="请使用 audio/wav 上传原始 WAV 录音")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="录音请求长度无效") from None
        if size < 0:
            raise HTTPException(status_code=400, detail="录音请求长度无效")
        if size > MAX_VOICE_BYTES:
            raise HTTPException(status_code=413, detail="录音文件过大，请录制不超过 30 秒的语音")
    if not asr.available:
        raise HTTPException(status_code=503, detail="电脑尚未配置中文语音识别模型，请完成模型配置后重试")
    gate = _voice_lock
    if gate.locked():
        raise HTTPException(status_code=503, detail="正在处理另一段语音，请稍后重试", headers={"Retry-After": "2"})
    await gate.acquire()
    release_on_exit = True
    try:
        try:
            data = await asyncio.wait_for(_read_voice_upload(request), VOICE_UPLOAD_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="录音上传超时，请检查网络后重新录音") from None
        _validate_voice_wav(data)
        task = asyncio.create_task(asr.recognize_wav(data))
        try:
            # The ASR adapter performs model loading and inference in to_thread.
            result = await asyncio.wait_for(asyncio.shield(task), VOICE_TRANSCRIBE_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            release_on_exit = False
            task.add_done_callback(lambda finished: _finish_voice_task(finished, gate))
            if isinstance(error, asyncio.TimeoutError):
                raise HTTPException(status_code=504, detail="语音识别超时，请稍后重试或缩短录音") from None
            raise
        except Exception as error:
            logger.warning("Family voice transcription failed: %s", error)
            raise HTTPException(status_code=503, detail="电脑中文语音识别暂不可用，请检查本地模型配置后重试") from None
        text = str(result.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="未识别到有效语音，请清晰说话后重新录音")
        if len(text) > 500:
            raise HTTPException(status_code=400, detail="识别文字超过 500 字，请缩短录音后重试")
        # Recognition has no message or device side effect. The caller must
        # present this text for confirmation before POSTing /family/messages.
        return {"text": text, "mode": "transcript", "requires_confirmation": True}
    finally:
        if release_on_exit:
            gate.release()


@router.get("/api/family/bindings")
async def family_bindings(authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM device_family_bindings WHERE owner_username=? COLLATE NOCASE "
            "OR family_username=? COLLATE NOCASE ORDER BY id DESC", (username, username),
        ).fetchall()
    return [{**dict(row),
             "is_owner_device": row["owner_username"].casefold() == row["family_username"].casefold()}
            for row in rows]


@router.post("/api/family/devices")
async def register_owner_device(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id"))
    with db() as connection:
        # Claiming a device and inviting a family member must serialize their
        # ownership checks, including when separate worker processes race.
        connection.execute("BEGIN IMMEDIATE")
        current_owner = connection.execute(
            "SELECT owner_username FROM device_family_bindings WHERE device_id=? ORDER BY id LIMIT 1",
            (device_id,),
        ).fetchone()
        if current_owner and current_owner["owner_username"].casefold() != username.casefold():
            raise HTTPException(status_code=403, detail="此设备已由其他佩戴者账号管理")
        existing = connection.execute(
            "SELECT id FROM device_family_bindings WHERE device_id=? "
            "AND owner_username=? COLLATE NOCASE AND family_username=? COLLATE NOCASE",
            (device_id, username, username),
        ).fetchone()
        if existing:
            binding_id = existing["id"]
        else:
            result = connection.execute(
                "INSERT INTO device_family_bindings(device_id,owner_username,family_username,"
                "can_view_camera,can_view_location,can_send_messages,created_at) VALUES(?,?,?,1,1,1,?)",
                (device_id, username, username, now_iso()),
            )
            binding_id = result.lastrowid
    return {"created": existing is None, "id": binding_id, "device_id": device_id,
            "owner_username": username, "family_username": username, "is_owner_device": True}


@router.post("/api/family/bindings")
async def create_family_binding(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id"))
    family_username = str(payload.get("family_username") or "").strip()
    if not 1 <= len(family_username) <= 80 or family_username.lower() == username.lower():
        raise HTTPException(status_code=422, detail="请输入其他已注册家属账号")
    permissions = {
        key: payload.get(key, True)
        for key in ("can_view_camera", "can_view_location", "can_send_messages")
    }
    if any(not isinstance(value, bool) for value in permissions.values()):
        raise HTTPException(status_code=422, detail="家属权限必须是布尔值")
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_owner = connection.execute(
            "SELECT owner_username FROM device_family_bindings WHERE device_id=? ORDER BY id LIMIT 1",
            (device_id,),
        ).fetchone()
        if current_owner and current_owner["owner_username"].lower() != username.lower():
            raise HTTPException(status_code=403, detail="此设备已由其他佩戴者账号管理")
        if not connection.execute("SELECT 1 FROM auth_users WHERE username=? COLLATE NOCASE", (family_username,)).fetchone():
            raise HTTPException(status_code=404, detail="家属账号不存在，请先完成注册")
        try:
            connection.execute(
                "INSERT INTO device_family_bindings(device_id,owner_username,family_username,can_view_camera,can_view_location,can_send_messages,created_at) VALUES(?,?,?,?,?,?,?)",
                (device_id, username, family_username, int(permissions["can_view_camera"]), int(permissions["can_view_location"]), int(permissions["can_send_messages"]), now_iso()),
            )
        except Exception as error:
            if "UNIQUE" in str(error).upper():
                raise HTTPException(status_code=409, detail="该家属已绑定此设备") from None
            raise
    return {"created": True, "device_id": device_id, "owner_username": username, "family_username": family_username}


@router.delete("/api/family/bindings/{binding_id}")
async def delete_family_binding(binding_id: int, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    with db() as connection:
        result = connection.execute(
            "DELETE FROM device_family_bindings WHERE id=? AND owner_username=? COLLATE NOCASE",
            (binding_id, username),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="绑定不存在或当前账号不是佩戴者")
    return {"deleted": True}


@router.get("/api/family/overview")
async def family_overview(device_id: str | None = None, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    if device_id is None:
        with db() as connection:
            row = connection.execute(
                "SELECT device_id FROM device_family_bindings WHERE owner_username=? COLLATE NOCASE "
                "OR family_username=? COLLATE NOCASE ORDER BY id LIMIT 1", (username, username),
            ).fetchone()
        if row is None:
            return {"bound": False, "bindings": []}
        device_id = row["device_id"]
    device_id = _device(device_id)
    binding = _binding(username, device_id)
    frame = camera_frames.get(device_id) if binding["can_view_camera"] == 1 else None
    with db() as connection:
        device = connection.execute("SELECT * FROM device_info WHERE device_id=?", (device_id,)).fetchone()
        alert = connection.execute(
            "SELECT * FROM obstacle_logs WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,),
        ).fetchone()
    if alert:
        try:
            if datetime.fromisoformat(alert["created_at"]) < datetime.now(timezone.utc) - timedelta(minutes=2):
                alert = None
        except (TypeError, ValueError):
            alert = None
    location = runtime.locations.get(device_id, {
        "available": False, "status": "awaiting_scan", "detail": "等待 ESP32 上传 Wi-Fi 扫描结果",
    }) if binding["can_view_location"] == 1 else {"available": False, "status": "not_authorized", "detail": "当前授权不包含位置查看"}
    return {
        "bound": True,
        "device_id": device_id,
        "role": "owner" if binding["owner_username"].lower() == username.lower() else "family",
        "permissions": {"camera": bool(binding["can_view_camera"]), "location": bool(binding["can_view_location"]), "messages": bool(binding["can_send_messages"])},
        "device": {
            "name": (device["name"] if device else device_id),
            "online": device_id in runtime.devices,
            "last_heartbeat": device["last_heartbeat"] if device else None,
            "firmware_version": device["firmware_version"] if device else None,
            "camera_ok": bool(device["camera_ok"]) if device and device["camera_ok"] is not None else frame is not None,
            "mic_ok": bool(device["mic_ok"]) if device and device["mic_ok"] is not None else None,
        },
        "camera": {"online": frame is not None, "seq": frame.seq if frame else None, "updated_at": now_iso() if frame else None},
        "speech": _speech_status(device_id, authorized=bool(binding["can_send_messages"])),
        "location": location,
        "current_alert": dict(alert) if alert else None,
        "updated_at": now_iso(),
    }


@router.get("/api/family/cameras/{device_id}/frame")
async def family_camera_frame(device_id: str, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(device_id)
    binding = _binding(username, device_id)
    if binding["can_view_camera"] != 1:
        raise HTTPException(status_code=403, detail="当前授权不包含画面查看")
    frame = camera_frames.get(device_id)
    if frame is None:
        raise HTTPException(status_code=410, detail="摄像头未开启或画面已过期")
    _log_camera_view(username, device_id)
    return Response(frame.image, media_type="image/jpeg", headers={
        "Cache-Control": "no-store, private", "X-Frame-Seq": str(frame.seq),
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/api/family/messages")
async def family_messages(device_id: str, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(device_id)
    _binding(username, device_id)
    with db() as connection:
        rows = connection.execute(
            "SELECT id,device_id,sender_username,recipient_username,body,message_type,created_at,read_at "
            "FROM family_messages WHERE device_id=? ORDER BY id DESC LIMIT 50", (device_id,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


@router.post("/api/family/messages")
async def send_family_message(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id"))
    binding = _binding(username, device_id, write=True)
    body = payload.get("body")
    if not isinstance(body, str):
        raise HTTPException(status_code=422, detail="消息内容必须是文字")
    body = body.strip()
    if not 1 <= len(body) <= 500:
        raise HTTPException(status_code=422, detail="消息长度需要在 1 到 500 字之间")
    message_type = payload.get("message_type", "text")
    if message_type not in ("text", "voice_transcript"):
        raise HTTPException(status_code=422, detail="消息类型必须是 text 或 voice_transcript")
    try:
        body.encode("gbk")
    except UnicodeEncodeError:
        raise HTTPException(status_code=422, detail="当前帽端暂不支持表情或特殊字符，请移除后再发送") from None
    recipient = binding["owner_username"] if binding["owner_username"].lower() != username.lower() else binding["family_username"]
    created_at = now_iso()
    with db() as connection:
        result = connection.execute(
            "INSERT INTO family_messages(device_id,sender_username,recipient_username,body,message_type,created_at) VALUES(?,?,?,?,?,?)",
            (device_id, username, recipient, body, message_type, created_at),
        )
        message_id = result.lastrowid
    message = {"id": message_id, "device_id": device_id, "sender_username": username, "recipient_username": recipient, "body": body, "message_type": message_type, "created_at": created_at, "read_at": None}
    message["delivery"] = _deliver_message(device_id, body)
    # The legacy web/tablet socket pool includes clients without this device's
    # family binding. Message content stays on the authorized HTTP endpoints.
    _log(username, device_id, "send_message")
    return message
