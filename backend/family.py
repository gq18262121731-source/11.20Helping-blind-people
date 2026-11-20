"""Authenticated family-assistance APIs for the first remote-support release."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Response

from .auth import require_session
from .camera_stream import SOURCE_ID, camera_frames
from .database import db, now_iso
from .services import runtime

router = APIRouter()


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
            "ORDER BY id LIMIT 1", (device_id, username, username),
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


@router.get("/api/family/bindings")
async def family_bindings(authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM device_family_bindings WHERE owner_username=? COLLATE NOCASE "
            "OR family_username=? COLLATE NOCASE ORDER BY id DESC", (username, username),
        ).fetchall()
    return [dict(row) for row in rows]


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
    _log(username, device_id, "view_camera_frame")
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
    body = str(payload.get("body") or "").strip()
    if not 1 <= len(body) <= 500:
        raise HTTPException(status_code=422, detail="消息长度需要在 1 到 500 字之间")
    recipient = binding["owner_username"] if binding["owner_username"].lower() != username.lower() else binding["family_username"]
    created_at = now_iso()
    with db() as connection:
        result = connection.execute(
            "INSERT INTO family_messages(device_id,sender_username,recipient_username,body,created_at) VALUES(?,?,?,?,?)",
            (device_id, username, recipient, body, created_at),
        )
        message_id = result.lastrowid
    message = {"id": message_id, "device_id": device_id, "sender_username": username, "recipient_username": recipient, "body": body, "message_type": "text", "created_at": created_at, "read_at": None}
    await runtime.send_device(device_id, {"type": "family_message", "body": body})
    await runtime.broadcast({"type": "family_message", **message})
    _log(username, device_id, "send_message")
    return message
