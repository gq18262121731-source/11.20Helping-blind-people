"""Shared location, map, navigation and LAN guard APIs.

The active location source is persisted per device and bounds a trip session.
Map calls use the configured AMap REST gateway; deterministic map results
are available only in explicitly enabled development/test environments.
"""
from __future__ import annotations

import asyncio
import math
import secrets
import time
import io
import wave
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .auth import require_session, session_username
from .asr import ASRError, asr
from .camera_stream import camera_frames
from .config import settings
from .database import db, now_iso
from .services import runtime
from .tts_delivery import tts_delivery

router = APIRouter()
LOCATION_MAX_AGE_SECONDS = 15
_guard_clients: dict[str, set[WebSocket]] = {}
_guard_sessions: dict[WebSocket, tuple[str, str, bool, str]] = {}
_demo_tasks: dict[str, asyncio.Task] = {}
_navigations: dict[str, dict[str, Any]] = {}
_map_voice_lock = asyncio.Lock()
_camera_view_last: dict[tuple[str, str], float] = {}


def _wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    if not (72.004 <= longitude <= 137.8347 and .8293 <= latitude <= 55.8271):
        return latitude, longitude
    a, ee = 6378245.0, .00669342162296594323
    x, y = longitude - 105.0, latitude - 35.0
    dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlat += (20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi))*2/3
    dlat += (160*math.sin(y/12*math.pi)+320*math.sin(y*math.pi/30))*2/3
    dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*math.pi)+20*math.sin(2*x*math.pi))*2/3
    dlon += (20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi))*2/3
    dlon += (150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    rad = latitude / 180 * math.pi
    magic = 1 - ee * math.sin(rad) ** 2
    return latitude + dlat * 180 / ((a * (1-ee) / magic**1.5) * math.pi), longitude + dlon * 180 / (a / math.sqrt(magic) / math.cos(rad) * math.pi)


def _gcj02_to_wgs84(latitude: float, longitude: float) -> tuple[float, float]:
    lat, lng = latitude, longitude
    for _ in range(3):
        glat, glng = _wgs84_to_gcj02(lat, lng)
        lat += latitude - glat
        lng += longitude - glng
    return lat, lng


def _device(value: Any) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise HTTPException(status_code=422, detail="设备编号格式无效")
    return value


def _location(data: dict[str, Any], device_id: str) -> dict[str, Any]:
    try:
        lat = float(data["latitude"])
        lng = float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="请提供有效的 latitude 和 longitude") from None
    if not math.isfinite(lat) or not -90 <= lat <= 90 or not math.isfinite(lng) or not -180 <= lng <= 180:
        raise HTTPException(status_code=422, detail="坐标范围无效")
    try:
        accuracy = float(data["accuracy"]) if data.get("accuracy") is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="accuracy 必须是有效数字") from None
    if accuracy is not None and (not math.isfinite(accuracy) or accuracy < 0):
        raise HTTPException(status_code=422, detail="accuracy 必须是非负有限数字")
    captured_at = str(data.get("captured_at") or now_iso())
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="captured_at 必须是 ISO 时间") from None
    source = str(data.get("source") or "browser_geolocation")
    if source not in ("browser_geolocation", "mock"):
        raise HTTPException(status_code=422, detail="当前只接受本机系统定位和演示定位；GNSS 硬件尚未接入")
    mode = "demo" if source == "mock" or data.get("mode") == "demo" else "real"
    return {"device_id": device_id, "latitude": lat, "longitude": lng,
            "accuracy": accuracy, "speed": data.get("speed"), "heading": data.get("heading"),
            "source": source, "mode": mode, "captured_at": captured_at,
            "sequence": int(data.get("sequence") or int(time.time() * 1000))}


def _expired(item: dict[str, Any] | None) -> bool:
    if not item:
        return True
    try:
        captured = datetime.fromisoformat(str(item["captured_at"]).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        return age > LOCATION_MAX_AGE_SECONDS or age < -300
    except (KeyError, TypeError, ValueError):
        return True


def _latest_location(device_id: str):
    provider = _active_provider(device_id)
    if provider and not provider["source"]:
        return None
    item = runtime.locations.get(device_id)
    if item and provider and (item.get("source") != provider["source"] or item.get("captured_at", "") < provider["started_at"]):
        item = None
    if item is None:
        with db() as connection:
            if provider:
                row = connection.execute("SELECT * FROM location_points WHERE device_id=? AND source=? AND captured_at>=? ORDER BY id DESC LIMIT 1", (device_id, provider["source"], provider["started_at"])).fetchone()
            else:
                row = connection.execute("SELECT * FROM location_points WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        if row:
            item = {"device_id":row["device_id"],"latitude":row["latitude"],"longitude":row["longitude"],"accuracy":row["accuracy"],"source":row["source"],"mode":row["mode"],"captured_at":row["captured_at"],"sequence":row["sequence"]}
            runtime.locations[device_id] = item
    return item


def _distance_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    radius = 6_371_000
    lat1, lat2 = math.radians(float(a["latitude"])), math.radians(float(b["latitude"]))
    dlat = lat2 - lat1
    dlon = math.radians(float(b["longitude"]) - float(a["longitude"]))
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return radius * 2 * math.asin(min(1, math.sqrt(h)))


def _segment_distance_m(point: dict[str, Any], a: list[float], b: list[float]) -> float:
    radius = 6_371_000
    ref_lat = math.radians(float(point["latitude"]))
    def xy(lon, lat):
        return radius * math.radians(float(lon)) * math.cos(ref_lat), radius * math.radians(float(lat))
    px, py = xy(point["longitude"], point["latitude"])
    ax, ay = xy(a[0], a[1]); bx, by = xy(b[0], b[1])
    dx, dy = bx-ax, by-ay
    length = dx*dx + dy*dy
    ratio = 0 if length == 0 else max(0, min(1, ((px-ax)*dx+(py-ay)*dy)/length))
    return math.hypot(px-(ax+ratio*dx), py-(ay+ratio*dy))


def _route_measure(point: dict[str, Any], points: list[list[float]]) -> tuple[float,float,float]:
    points=[p for p in points if isinstance(p,list) and len(p)==2]
    if len(points)<2:return 0.0,0.0,0.0
    legs=[_distance_m({"longitude":a[0],"latitude":a[1]},{"longitude":b[0],"latitude":b[1]}) for a,b in zip(points,points[1:])]
    total=sum(legs); accumulated=0.0; best=float("inf"); travelled=0.0
    ref=math.radians(float(point["latitude"]))
    def xy(lon,lat): return 6_371_000*math.radians(float(lon))*math.cos(ref),6_371_000*math.radians(float(lat))
    px,py=xy(point["longitude"],point["latitude"])
    for (a,b),leg in zip(zip(points,points[1:]),legs):
        ax,ay=xy(a[0],a[1]);bx,by=xy(b[0],b[1]);dx,dy=bx-ax,by-ay;squared=dx*dx+dy*dy
        ratio=0 if squared==0 else max(0,min(1,((px-ax)*dx+(py-ay)*dy)/squared))
        error=math.hypot(px-(ax+ratio*dx),py-(ay+ratio*dy))
        if error<best:best=error;travelled=accumulated+ratio*leg
        accumulated+=leg
    return best,travelled,total


def _advance_navigation(device_id: str, location: dict[str, Any]):
    nav = _navigations.get(device_id)
    if not nav or nav["status"] not in ("ROUTE_READY", "NAVIGATING", "OFF_ROUTE", "PAUSED"):
        return None
    if location.get("source") != nav.get("location_source") or location.get("mode") != nav.get("location_mode"):
        return None
    destination = nav["destination"]
    current = location
    route_points = nav["route"].get("polyline", [])
    valid_points = [point for point in route_points if isinstance(point, list) and len(point) == 2]
    deviation,travelled,total=_route_measure(current,valid_points)
    direct_remaining=_distance_m(current,destination)
    remaining=direct_remaining if total<=0 else max(0.0,total-travelled)
    arrived=direct_remaining<=20
    nav["remaining_m"] = 0 if arrived else round(remaining)
    nav["progress"] = round(max(0.0,min(.99,travelled/total if total>0 else 1-remaining/max(nav.get("direct_distance",remaining),1))),3)
    previous_status = nav["status"]
    nav["status"] = "ARRIVED" if arrived else "OFF_ROUTE" if deviation > max(100,(location.get("accuracy") or 0)*2) else "NAVIGATING"
    if nav["route"].get("mode") == "amap" and nav["status"] != previous_status and nav["status"] in ("OFF_ROUTE", "ARRIVED"):
        title = {"OFF_ROUTE":"路线偏离", "ARRIVED":"已到达目的地"}[nav["status"]]
        _record_guard_event(device_id, "navigation_" + nav["status"].lower(), title, nav.get("destination_name") or "")
    steps=nav["route"].get("steps",[])
    if steps:
        accumulated=0.0
        for index,step in enumerate(steps):
            try: distance=max(0.0,float(step.get("distance") or 0))
            except (TypeError,ValueError): distance=0.0
            if travelled<=accumulated+distance or index==len(steps)-1:
                nav["current_step_index"]=index;nav["current_step"]=step;break
            accumulated+=distance
    nav["updated_at"] = location["captured_at"]
    return nav


def _record_guard_event(device_id: str, event_type: str, title: str, detail: str = "", source_mode: str = "real"):
    with db() as connection:
        connection.execute(
            "INSERT INTO guard_events(device_id,event_type,title,detail,occurred_at,source_mode) VALUES(?,?,?,?,?,?)",
            (device_id, event_type, title, detail[:500], now_iso(), "demo" if source_mode == "demo" else "real"),
        )


def _active_provider(device_id: str):
    with db() as connection:
        row = connection.execute("SELECT source,started_at FROM guard_location_provider WHERE device_id=?", (device_id,)).fetchone()
    return dict(row) if row else None


def _set_provider(device_id: str, source: str | None, started_at: str | None = None):
    current = _active_provider(device_id)
    stored_source = source or ""
    changed = not current or current["source"] != stored_source
    with db() as connection:
        connection.execute(
            "INSERT INTO guard_location_provider(device_id,source,started_at) VALUES(?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET source=excluded.source,started_at=excluded.started_at",
            (device_id, stored_source, started_at or now_iso()),
        )
    if changed:
        runtime.locations.pop(device_id, None)
        _navigations.pop(device_id, None)


async def _publish(device_id: str, event: dict[str, Any]):
    # Location is private and must stay on device-authorized guard sockets.
    if event.get("type") == "obstacle_alert":
        _record_guard_event(device_id, "warning_event", "环境预警", event.get("voice_text") or event.get("object") or "检测到需要留意的目标", "demo" if event.get("simulation") else "real")
    await publish_guard_event(device_id, event)


async def publish_guard_event(device_id: str, event: dict[str, Any]):
    device_id = str(device_id)
    camera_event = event.get("type") in ("detection_update", "spatial_update", "obstacle_alert")
    for ws in list(_guard_clients.get(device_id, ())):
        identity = _guard_sessions.get(ws)
        if not identity or identity[1] != device_id:
            _guard_clients[device_id].discard(ws)
            continue
        try:
            if session_username(identity[3]) != identity[0]:
                await ws.close(code=4401, reason="登录状态已失效")
                _guard_clients[device_id].discard(ws)
                _guard_sessions.pop(ws, None)
                continue
            _binding_or_owner(identity[0], device_id, location=True)
            if not identity[2] and event.get("type") == "location_update" and event.get("location", {}).get("mode") == "demo":
                continue
            if not identity[2] and event.get("type") == "navigation_update" and event.get("navigation", {}).get("route", {}).get("mode") == "mock":
                continue
            if camera_event and (event.get("simulation") or device_id.startswith("simulator_")):
                continue
            if camera_event:
                try:
                    _binding_or_owner(identity[0], device_id, camera=True)
                except HTTPException:
                    continue
            await ws.send_json(event)
        except HTTPException:
            try:
                await ws.close(code=4403, reason="设备共享授权已撤销")
            except RuntimeError:
                pass
            _guard_clients[device_id].discard(ws)
            _guard_sessions.pop(ws, None)
        except Exception:
            _guard_clients.get(device_id, set()).discard(ws)
            _guard_sessions.pop(ws, None)


def _binding_or_owner(username: str, device_id: str, *, location=False, camera=False):
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM device_family_bindings WHERE device_id=? AND "
            "(owner_username=? COLLATE NOCASE OR family_username=? COLLATE NOCASE) LIMIT 1",
            (device_id, username, username),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="当前账号没有此设备的家属授权")
    if location and row["can_view_location"] != 1:
        raise HTTPException(status_code=403, detail="当前授权不允许查看位置")
    if camera and row["can_view_camera"] != 1:
        raise HTTPException(status_code=403, detail="当前授权不允许查看视频")
    if row["owner_username"].casefold() != username.casefold():
        with db() as connection:
            sharing = connection.execute("SELECT enabled FROM guard_sharing WHERE device_id=?", (device_id,)).fetchone()
        if sharing is not None and sharing["enabled"] != 1:
            raise HTTPException(status_code=403, detail="佩戴者已暂停位置和视频共享")
    return row


@router.get("/api/v1/guard/devices")
async def list_guard_devices(authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    with db() as connection:
        rows = connection.execute("SELECT b.*,s.enabled AS sharing_enabled,d.name,p.source AS location_source FROM device_family_bindings b LEFT JOIN guard_sharing s ON s.device_id=b.device_id LEFT JOIN device_info d ON d.device_id=b.device_id LEFT JOIN guard_location_provider p ON p.device_id=b.device_id WHERE b.owner_username=? COLLATE NOCASE OR b.family_username=? COLLATE NOCASE ORDER BY b.id DESC", (username, username)).fetchall()
    return [{"device_id": row["device_id"], "name": row["name"] or row["device_id"],
             "owner": row["owner_username"].casefold() == username.casefold(),
             "permissions": {"location": bool(row["can_view_location"]), "camera": bool(row["can_view_camera"])},
             "location_source": row["location_source"] or None,
             "sharing": True if row["sharing_enabled"] is None else bool(row["sharing_enabled"])} for row in rows]


@router.post("/api/v1/guard/devices")
async def create_guard_device(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    name = str(payload.get("name") or "启明辅行设备").strip()[:80]
    device_id = "guard_" + secrets.token_hex(8)
    with db() as connection:
        connection.execute("INSERT INTO device_info(device_id,name,online,updated_at) VALUES(?,?,0,?)", (device_id, name, now_iso()))
        connection.execute("INSERT INTO device_family_bindings(device_id,owner_username,family_username,can_view_camera,can_view_location,can_send_messages,created_at) VALUES(?,?,?,1,1,1,?)", (device_id, username, username, now_iso()))
        connection.execute("INSERT INTO guard_sharing(device_id,enabled,updated_at) VALUES(?,1,?)", (device_id, now_iso()))
    return {"device_id": device_id, "name": name, "owner": True, "sharing": True,
            "permissions": {"location": True, "camera": True}}


@router.post("/api/v1/sharing/{action}")
async def set_sharing(action: str, payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    if action not in ("start", "stop"):
        raise HTTPException(status_code=404, detail="未知共享操作")
    device_id = _device(payload.get("device_id"))
    binding = _binding_or_owner(username, device_id)
    if binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=403, detail="只有佩戴者可以控制共享")
    enabled = int(action == "start")
    with db() as connection:
        connection.execute("INSERT INTO guard_sharing(device_id,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at", (device_id, enabled, now_iso()))
        connection.execute("INSERT INTO family_access_logs(device_id,actor_username,action,created_at) VALUES(?,?,?,?)", (device_id, username, "sharing_" + action, now_iso()))
    await _publish(device_id, {"type": "sharing_status", "enabled": bool(enabled), "updated_at": now_iso()})
    if not enabled:
        for ws in list(_guard_clients.get(device_id, ())):
            identity = _guard_sessions.get(ws)
            if identity and not identity[2]:
                try:
                    await ws.close(code=4403, reason="佩戴者已暂停共享")
                except RuntimeError:
                    pass
                _guard_clients[device_id].discard(ws)
                _guard_sessions.pop(ws, None)
    return {"device_id": device_id, "sharing": bool(enabled)}


def _record_camera_view(username: str, device_id: str):
    key = (username.casefold(), device_id)
    now = time.monotonic()
    if now - _camera_view_last.get(key, -60.0) < 30:
        return
    with db() as connection:
        connection.execute("INSERT INTO family_access_logs(device_id,actor_username,action,created_at) VALUES(?,?,?,?)", (device_id, username, "view_camera_frame", now_iso()))
    _camera_view_last[key] = now
    if len(_camera_view_last) > 2048:
        for old in sorted(_camera_view_last, key=_camera_view_last.get)[:512]:
            _camera_view_last.pop(old, None)


@router.post("/api/v1/location/report")
async def report_location(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001"))
    binding = _binding_or_owner(username, device_id, location=True)
    if binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=403, detail="只有佩戴者设备可以上报位置")
    item = _location(payload, device_id)
    provider = _active_provider(device_id)
    if provider and provider["source"] and provider["source"] != item["source"]:
        raise HTTPException(status_code=409, detail="此设备已有其他定位来源在运行，请先切换定位来源")
    if not provider or not provider["source"]:
        _set_provider(device_id, item["source"], item["captured_at"])
    else:
        with db() as connection:
            first_point = connection.execute(
                "SELECT 1 FROM location_points WHERE device_id=? AND source=? AND captured_at>=? LIMIT 1",
                (device_id, item["source"], provider["started_at"]),
            ).fetchone()
        if not first_point:
            _set_provider(device_id, item["source"], item["captured_at"])
    runtime.locations[device_id] = item
    with db() as connection:
        previous = connection.execute("SELECT latitude,longitude,source FROM location_points WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        connection.execute("INSERT INTO location_points(device_id,latitude,longitude,accuracy,source,mode,captured_at,sequence) VALUES(?,?,?,?,?,?,?,?)", (device_id, item["latitude"], item["longitude"], item["accuracy"], item["source"], item["mode"], item["captured_at"], item["sequence"]))
        connection.execute("DELETE FROM location_points WHERE device_id=? AND id NOT IN (SELECT id FROM location_points WHERE device_id=? ORDER BY id DESC LIMIT 5000)", (device_id, device_id))
    if item["mode"] != "demo" and (not previous or previous["source"] != item["source"] or _distance_m(dict(previous), item) >= 100):
        _record_guard_event(device_id, "location_update", "位置已同步", f"精度约 {round(item['accuracy'] or 0)} 米", item["mode"])
    await _publish(device_id, {"type": "location_update", "location": item})
    nav = _advance_navigation(device_id, item)
    if nav:
        await _publish(device_id, {"type": "navigation_update", "navigation": nav})
    return {"accepted": True, "location": item, "expired": False}


@router.get("/api/v1/devices/status")
async def guard_device_status(device_id: str = "hat-001", authorization: str | None = Header(default=None)):
    username = require_session(authorization); device_id = _device(device_id)
    _binding_or_owner(username, device_id)
    point = _latest_location(device_id)
    frame = camera_frames.get(device_id)
    with db() as connection:
        row = connection.execute("SELECT name,last_heartbeat FROM device_info WHERE device_id=?", (device_id,)).fetchone()
        owner = connection.execute("SELECT owner_username FROM device_family_bindings WHERE device_id=? ORDER BY id LIMIT 1", (device_id,)).fetchone()
    if owner and owner["owner_username"].casefold() == username.casefold():
        frame = frame or camera_frames.get("local_camera")
    detection = runtime.latest.get(device_id)
    if owner and owner["owner_username"].casefold() == username.casefold():
        local_detection = runtime.latest.get("local_camera")
        if local_detection and (not detection or local_detection.get("at", 0) > detection.get("at", 0)):
            detection = local_detection
    visual = None
    if detection and time.time() - float(detection.get("at", 0)) <= 10:
        result = detection.get("result", {})
        visual = {"objects":result.get("objects", []), "model_status":result.get("model_status"), "updated_at":datetime.fromtimestamp(detection["at"], timezone.utc).isoformat()}
    online = device_id in runtime.devices
    if row and row["last_heartbeat"]:
        try:
            heartbeat = datetime.fromisoformat(row["last_heartbeat"].replace("Z", "+00:00"))
            if heartbeat.tzinfo is None: heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            online = online or (datetime.now(timezone.utc) - heartbeat).total_seconds() <= 30
        except ValueError:
            pass
    frame_age = round(max(0.0, time.monotonic() - frame.received_at) * 1000, 1) if frame else None
    return {"device_id":device_id,"name":row["name"] if row else device_id,
            "online":online,"last_heartbeat":row["last_heartbeat"] if row else None,
            "camera_online":frame is not None,"camera_seq":frame.seq if frame else None,"camera_frame_age_ms":frame_age,"location_fresh":not _expired(point),
            "visual_summary":visual,"updated_at":now_iso()}


@router.get("/api/v1/location/latest")
async def latest_location(device_id: str = "hat-001", authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(device_id)
    binding = _binding_or_owner(username, device_id, location=True)
    item = _latest_location(device_id)
    if item and item.get("mode") == "demo" and binding["owner_username"].casefold() != username.casefold():
        return {"available":False,"expired":False,"location":None,"source_label":"暂无位置"}
    return {"available": bool(item) and not _expired(item), "expired": _expired(item),
            "location": item, "source_label": _source_label(item)}


@router.get("/api/v1/location/history")
async def location_history(device_id: str = "hat-001", limit: int = 200, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(device_id)
    binding = _binding_or_owner(username, device_id, location=True)
    provider = _active_provider(device_id)
    with db() as connection:
        if provider and provider["source"]:
            rows = connection.execute("SELECT * FROM location_points WHERE device_id=? AND source=? AND captured_at>=? ORDER BY id DESC LIMIT ?", (device_id, provider["source"], provider["started_at"], min(max(limit, 1), 1000))).fetchall()
        elif provider:
            rows = []
        else:
            latest = connection.execute("SELECT source FROM location_points WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
            rows = connection.execute("SELECT * FROM location_points WHERE device_id=? AND source=? ORDER BY id DESC LIMIT ?", (device_id, latest["source"] if latest else "", min(max(limit, 1), 1000))).fetchall()
    result = [dict(row) for row in rows]
    if binding["owner_username"].casefold() != username.casefold():
        result = [row for row in result if row["mode"] != "demo"]
    return result


@router.get("/api/v1/events")
async def guard_events(device_id: str = "hat-001", limit: int = 100, authorization: str | None = Header(default=None)):
    username = require_session(authorization); device_id = _device(device_id)
    _binding_or_owner(username, device_id, location=True)
    limit = min(max(limit, 1), 300)
    with db() as connection:
        alerts = connection.execute("SELECT id,device_id,detected_at,object_class,direction,voice_text,confidence FROM obstacle_logs WHERE device_id=? AND device_id NOT LIKE 'simulator_%' ORDER BY id DESC LIMIT ?", (device_id,limit)).fetchall()
        accesses = connection.execute("SELECT id,action,created_at FROM family_access_logs WHERE device_id=? AND actor_username=? COLLATE NOCASE ORDER BY id DESC LIMIT ?", (device_id,username,limit)).fetchall()
        guard_rows = connection.execute("SELECT id,event_type,title,detail,occurred_at FROM guard_events WHERE device_id=? AND source_mode='real' AND event_type!='warning_event' ORDER BY id DESC LIMIT ?", (device_id,limit)).fetchall()
    events = [{"id":f"alert-{row['id']}","type":"warning_event","title":"视觉预警","detail":f"{row['object_class'] or '未知目标'} · {row['direction'] or '方位未知'} · {row['voice_text'] or ''}".strip(" ·"),"occurred_at":row["detected_at"]} for row in alerts]
    events += [{"id":f"guard-{row['id']}","type":row["event_type"],"title":row["title"],"detail":row["detail"],"occurred_at":row["occurred_at"]} for row in guard_rows]
    events += [{"id":f"access-{row['id']}","type":row["action"],"title":"共享状态已变更","detail":"已开始位置和视频共享" if row["action"]=="sharing_start" else "已暂停位置和视频共享" if row["action"]=="sharing_stop" else "授权访问记录","occurred_at":row["created_at"]} for row in accesses if str(row["action"]).startswith("sharing_")]
    return sorted(events,key=lambda item:item["occurred_at"] or "",reverse=True)[:limit]


def _source_label(item):
    if not item:
        return "暂无位置"
    return {"browser_geolocation": "本机系统定位", "mock": "模拟定位", "gnss": "GNSS 定位"}.get(item.get("source"), "定位来源未知")


@router.post("/api/v1/location/simulate/start")
async def start_simulation(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001"))
    binding = _binding_or_owner(username, device_id)
    if binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=403, detail="只有佩戴者可以启动演示定位")
    _set_provider(device_id, "mock")
    await _publish(device_id, {"type":"location_provider","source":"mock","enabled":True})
    if device_id in _demo_tasks and not _demo_tasks[device_id].done():
        _demo_tasks[device_id].cancel()
    start = payload.get("start") or {"latitude": 31.2304, "longitude": 121.4737}
    end = payload.get("end") or {"latitude": 31.236, "longitude": 121.480}
    try:
        points = [(float(start["latitude"] + (end["latitude"] - start["latitude"]) * i / 20), float(start["longitude"] + (end["longitude"] - start["longitude"]) * i / 20)) for i in range(21)]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="演示起止点无效") from None
    async def run():
        for seq, (lat, lng) in enumerate(points, 1):
            item = _location({"device_id": device_id, "latitude": lat, "longitude": lng, "accuracy": 12, "source": "mock", "mode": "demo", "sequence": seq}, device_id)
            runtime.locations[device_id] = item
            with db() as connection:
                connection.execute("INSERT INTO location_points(device_id,latitude,longitude,accuracy,source,mode,captured_at,sequence) VALUES(?,?,?,?,?,?,?,?)", (device_id, lat, lng, 12, "mock", "demo", item["captured_at"], seq))
            await _publish(device_id, {"type": "location_update", "location": item})
            nav = _advance_navigation(device_id, item)
            if nav:
                await _publish(device_id, {"type": "navigation_update", "navigation": nav})
            try:
                interval = min(2.0, max(0.2, float(payload.get("interval", 0.35))))
            except (TypeError, ValueError):
                interval = 0.35
            await asyncio.sleep(interval)
    _demo_tasks[device_id] = asyncio.create_task(run())
    return {"started": True, "status": "running", "device_id": device_id, "mode": "demo"}


@router.post("/api/v1/location/simulate/stop")
async def stop_simulation(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001"))
    binding = _binding_or_owner(username, device_id)
    if binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=403, detail="只有佩戴者可以停止演示定位")
    task = _demo_tasks.pop(device_id, None)
    if task and not task.done():
        task.cancel()
    provider = _active_provider(device_id)
    if provider and provider["source"] == "mock":
        _set_provider(device_id, None)
        await _publish(device_id, {"type":"location_provider","source":None,"enabled":False})
    return {"stopped": bool(task)}


@router.post("/api/v1/location/provider")
async def set_location_provider(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id"))
    binding = _binding_or_owner(username, device_id)
    if binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=403, detail="只有佩戴者可以切换定位来源")
    source = payload.get("source")
    enabled = bool(payload.get("enabled", True))
    if enabled and source not in ("browser_geolocation", "mock"):
        raise HTTPException(status_code=422, detail="定位来源无效")
    current = _active_provider(device_id)
    if enabled:
        if current and current["source"] == source:
            return {"device_id":device_id,"source":source,"enabled":True}
        task = _demo_tasks.pop(device_id, None) if source != "mock" else None
        if task and not task.done(): task.cancel()
        _set_provider(device_id, source)
    elif not current or current["source"] == source:
        _set_provider(device_id, None)
    await _publish(device_id, {"type":"location_provider","source":source if enabled else None,"enabled":enabled})
    return {"device_id":device_id,"source":source if enabled else None,"enabled":enabled}


async def _amap(path: str, params: dict[str, Any]):
    # The gateway key belongs only in server-side request headers.  It is not
    # an official AMap key and must never reach the JS SDK or its query string.
    gateway = settings.map_service_mode == "cocomap"
    if settings.map_service_mode not in ("cocomap", "amap"):
        return None
    key = settings.cocomap_api_key if gateway else settings.amap_web_key
    if not key:
        return None
    clean_params = {name: value for name, value in params.items() if name.lower() not in ("key", "ak", "token", "authorization", "callback")}
    clean_params["output"] = "JSON"
    if gateway:
        base = settings.cocomap_base_url
        # This is trusted server configuration, never a caller-supplied URL.
        try:
            url = httpx.URL(base)
        except (httpx.InvalidURL, ValueError):
            return None
        if url.scheme != "https" or not url.host or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            return None
        url = base.rstrip("/") + "/v3/" + path
        request_headers = {"Authorization": "Bearer " + key, "X-Cocomap-Provider": "amap"}
    else:
        if key.startswith("sk_coco_"):
            return None
        url = "https://restapi.amap.com/v3/" + path
        clean_params["key"] = key
        request_headers = {}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(url, params=clean_params, headers=request_headers)
            response.raise_for_status()
            data = response.json()
            if gateway:
                if not isinstance(data, dict) or data.get("ok") is not True:
                    return None
                data = data.get("data")
            return data if isinstance(data, dict) and str(data.get("status")) == "1" else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


@router.get("/api/v1/map/config")
async def map_config(authorization: str | None = Header(default=None)):
    require_session(authorization)
    web_configured = bool(settings.cocomap_api_key) if settings.map_service_mode == "cocomap" else bool(settings.amap_web_key) if settings.map_service_mode == "amap" else False
    js_key = settings.amap_js_key if not settings.amap_js_key.startswith("sk_coco_") else ""
    js_provider = settings.map_js_provider if settings.map_js_provider in ("baidu", "amap") else "baidu"
    if js_provider == "baidu":
        sdk_key = settings.baidu_map_js_ak if not settings.baidu_map_js_ak.startswith("sk_coco_") else ""
    else:
        sdk_key = js_key
    return {"configured": bool(sdk_key), "mode": js_provider if sdk_key else "unavailable", "js_provider": js_provider, "js_key": sdk_key, "security_code": settings.amap_js_security_code if js_provider == "amap" and sdk_key else "", "service_mode": settings.map_service_mode, "web_configured": web_configured}


@router.post("/api/v1/map/voice/transcribe")
async def transcribe_map_voice(request: Request, device_id: str = "hat-001", authorization: str | None = Header(default=None)):
    username = require_session(authorization); device_id = _device(device_id)
    _binding_or_owner(username, device_id, location=True)
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "audio/wav":
        raise HTTPException(status_code=415, detail="请使用 16kHz 单声道 PCM WAV 音频")
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > 1_000_000:
                raise HTTPException(status_code=413, detail="录音超过 1 MB")
        except ValueError:
            raise HTTPException(status_code=400, detail="录音请求长度无效") from None
    async def read_audio():
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > 1_000_000:
                raise HTTPException(status_code=413, detail="录音超过 1 MB")
            chunks.extend(chunk)
        return bytes(chunks)
    try:
        data = await asyncio.wait_for(read_audio(), timeout=10)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="录音上传超时，请重试") from None
    if not data:
        raise HTTPException(status_code=400, detail="录音为空")
    try:
        with wave.open(io.BytesIO(data), "rb") as recording:
            if recording.getnchannels() != 1 or recording.getsampwidth() != 2 or recording.getframerate() != 16_000 or recording.getcomptype() != "NONE":
                raise HTTPException(status_code=400, detail="录音须为 16kHz、16bit、单声道 PCM WAV")
            if not .2 <= recording.getnframes()/16_000 <= 15:
                raise HTTPException(status_code=400, detail="录音长度须为 0.2 到 15 秒")
    except (wave.Error, EOFError, OSError):
        raise HTTPException(status_code=400, detail="录音文件无效，请重新录制") from None
    if not asr.available:
        raise HTTPException(status_code=503, detail="电脑中文语音识别模型尚未配置，请输入文字查询")
    if _map_voice_lock.locked():
        raise HTTPException(status_code=503, detail="语音识别正在处理另一段录音，请稍后重试")
    async with _map_voice_lock:
        try:
            result = await asr.recognize_wav(data)
        except ASRError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
    text = str(result.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="没有识别到语音，请重试或输入文字")
    return {"text":text[:500],"source":"existing_asr","requires_confirmation":True}


@router.post("/api/v1/map/voice/speak")
async def speak_map_reply(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id"))
    binding = _binding_or_owner(username, device_id, location=True)
    if binding["can_send_messages"] != 1:
        raise HTTPException(status_code=403, detail="当前授权不允许语音播报")
    message = str(payload.get("text") or "").strip()[:500]
    if not message:
        raise HTTPException(status_code=422, detail="播报内容不能为空")
    if not runtime.speech_enabled:
        raise HTTPException(status_code=409, detail="帽端语音播报已关闭")
    delivery = tts_delivery.queue_text(device_id, message, priority=2)
    if delivery.get("status") == "device_offline":
        raise HTTPException(status_code=503, detail="帽端 TTS 音频通道未连接")
    if delivery.get("status") == "output_unconfigured":
        raise HTTPException(status_code=409, detail="帽端 I2S 输出尚未配置")
    if delivery.get("status") == "asset_missing":
        raise HTTPException(status_code=404, detail="本地语音库未收录该文本")
    if delivery.get("status") == "synthesis_unavailable":
        raise HTTPException(status_code=503, detail="未配置 Edge TTS 或 FFmpeg")
    if delivery.get("status") == "synthesis_queue_full":
        raise HTTPException(status_code=429, detail="语音合成队列已满")
    if delivery.get("status") == "text_too_long":
        raise HTTPException(status_code=422, detail="播报文字不能超过 500 字")
    if delivery.get("status") not in {"queued", "synthesizing"}:
        raise HTTPException(status_code=503, detail="帽端语音队列暂不可用")
    return {"status":delivery.get("status", "queued"),"target":"device","device_id":device_id}


@router.post("/api/v1/map/reverse-geocode")
async def reverse_geocode(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001")); binding = _binding_or_owner(username, device_id, location=True)
    current_location = _latest_location(device_id)
    if current_location and current_location.get("mode") == "demo" and binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=409, detail="当前没有可供共享的位置数据")
    try:
        lat, lng = float(payload.get("latitude")), float(payload.get("longitude"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="经纬度必须是有效数字") from None
    if not math.isfinite(lat) or not -90 <= lat <= 90 or not math.isfinite(lng) or not -180 <= lng <= 180:
        raise HTTPException(status_code=422, detail="经纬度范围无效")
    glat, glng = _wgs84_to_gcj02(lat, lng)
    data = await _amap("geocode/regeo", {"location": f"{glng:.6f},{glat:.6f}"})
    if data and data.get("regeocode"):
        regeocode = data["regeocode"]
        return {"mode": "amap", "address": regeocode.get("formatted_address", ""), "district": regeocode.get("addressComponent", {}).get("district", ""), "street": regeocode.get("addressComponent", {}).get("streetNumber", {}).get("street", "")}
    if settings.map_demo_mode:
        return {"mode": "mock", "address": "演示地址", "district": "", "street": ""}
    raise HTTPException(status_code=503, detail="地址服务暂时不可用")


@router.post("/api/v1/map/search")
async def search_places(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001"))
    _binding_or_owner(username, device_id, location=True)
    keyword = str(payload.get("keyword") or "").strip()[:80]
    if not keyword:
        raise HTTPException(status_code=422, detail="请输入目的地名称")
    params = {"keywords": keyword, "offset": 10, "page": 1, "extensions": "base"}
    city = str(payload.get("city") or "").strip()[:60]
    if city:
        params["city"] = city
    data = await _amap("place/text", params)
    if data is None:
        raise HTTPException(status_code=503, detail="地点搜索暂时不可用")
    pois = []
    for poi in data.get("pois", []):
        try:
            gcj_lng, gcj_lat = (float(value) for value in poi.get("location", "").split(",", 1))
            if not math.isfinite(gcj_lng) or not math.isfinite(gcj_lat) or not -180 <= gcj_lng <= 180 or not -90 <= gcj_lat <= 90:
                continue
            latitude, longitude = _gcj02_to_wgs84(gcj_lat, gcj_lng)
        except (TypeError, ValueError, AttributeError):
            continue
        pois.append({"name": poi.get("name"), "address": poi.get("address") if isinstance(poi.get("address"), str) else "", "longitude": longitude, "latitude": latitude, "type": poi.get("type"), "distance": None})
    return {"mode": "amap", "keyword": keyword, "results": pois}


@router.post("/api/v1/map/nearby")
async def nearby(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001")); binding = _binding_or_owner(username, device_id, location=True)
    location = _latest_location(device_id)
    if _expired(location):
        raise HTTPException(status_code=409, detail="定位已过期，暂时不能搜索附近地点")
    if location.get("mode") == "demo" and binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=409, detail="当前没有可供共享的位置数据")
    if location.get("mode") == "demo" and not settings.map_demo_mode:
        raise HTTPException(status_code=409, detail="需要有效的位置数据才能搜索附近地点")
    keyword = str(payload.get("keyword") or "医院").strip()[:40]
    try:
        radius = min(max(int(payload.get("radius", 3000)), 100), 50000)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="搜索半径必须是整数") from None
    glat, glng = _wgs84_to_gcj02(location["latitude"], location["longitude"])
    data = await _amap("place/around", {"location": f"{glng:.6f},{glat:.6f}", "keywords": keyword, "radius": radius, "offset": 10, "sortrule": "distance"})
    if data:
        pois = []
        for poi in data.get("pois", []):
            try:
                gcj_lng, gcj_lat = (float(value) for value in poi.get("location", "").split(",", 1))
                latitude, longitude = _gcj02_to_wgs84(gcj_lat, gcj_lng)
            except (TypeError, ValueError):
                continue
            pois.append({"name":poi.get("name"),"address":poi.get("address"),"longitude":longitude,"latitude":latitude,"distance":poi.get("distance"),"type":poi.get("type")})
        return {"mode": "amap", "keyword": keyword, "results": pois}
    if settings.map_demo_mode:
        return {"mode": "mock", "keyword": keyword, "results": [{"name": f"{keyword}示例地点", "address": "课堂演示位置", "latitude": location["latitude"] + 0.002, "longitude": location["longitude"] + 0.002, "distance": 310, "type": keyword}]}
    raise HTTPException(status_code=503, detail="地点搜索暂时不可用")


@router.post("/api/v1/map/walking-route")
async def walking_route(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001")); binding = _binding_or_owner(username, device_id, location=True)
    origin = payload.get("origin") or _latest_location(device_id)
    destination = payload.get("destination")
    if not origin or not destination:
        raise HTTPException(status_code=422, detail="需要起点和终点坐标")
    if payload.get("origin") is None and _expired(origin):
        raise HTTPException(status_code=409, detail="定位已过期，暂时不能规划路线")
    if payload.get("origin") is None and origin.get("mode") == "demo" and binding["owner_username"].casefold() != username.casefold():
        raise HTTPException(status_code=409, detail="当前没有可供共享的位置数据")
    if payload.get("origin") is None and origin.get("mode") == "demo" and not settings.map_demo_mode:
        raise HTTPException(status_code=409, detail="需要有效的位置数据才能规划路线")
    try:
        olat, olng = float(origin["latitude"]), float(origin["longitude"]); dlat, dlng = float(destination["latitude"]), float(destination["longitude"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="起点或终点坐标无效") from None
    if any(not math.isfinite(value) for value in (olat,olng,dlat,dlng)) or not -90 <= olat <= 90 or not -90 <= dlat <= 90 or not -180 <= olng <= 180 or not -180 <= dlng <= 180:
        raise HTTPException(status_code=422, detail="起点或终点坐标超出范围")
    glat, glng = _wgs84_to_gcj02(olat, olng)
    dgcj_lat, dgcj_lng = _wgs84_to_gcj02(dlat, dlng)
    data = await _amap("direction/walking", {"origin": f"{glng:.6f},{glat:.6f}", "destination": f"{dgcj_lng:.6f},{dgcj_lat:.6f}"})
    if data and data.get("route", {}).get("paths"):
        path = data["route"]["paths"][0]
        polyline = []
        for step in path.get("steps", []):
            for point in str(step.get("polyline", "")).split(";"):
                try:
                    lng, lat = (float(value) for value in point.split(",", 1))
                    lat, lng = _gcj02_to_wgs84(lat, lng)
                    polyline.append([lng, lat])
                except (TypeError, ValueError):
                    continue
        route = {"mode": "amap", "distance": int(path.get("distance", 0)), "duration": int(path.get("duration", 0)), "polyline": polyline, "steps": [{"instruction": s.get("instruction"), "distance": s.get("distance")} for s in path.get("steps", [])]}
    elif settings.map_demo_mode:
        distance = int(math.hypot((dlat - olat) * 111000, (dlng - olng) * 93000)); route = {"mode": "mock", "distance": distance, "duration": max(60, distance // 1), "polyline": [[olng, olat], [dlng, dlat]], "steps": [{"instruction": "沿示例路线直行", "distance": distance}]}
    else:
        raise HTTPException(status_code=503, detail="路线服务暂时不可用")
    route_origin = {"latitude": olat, "longitude": olng}
    result = {"device_id": device_id, "status": "ROUTE_READY", "route": route, "destination": {"latitude":dlat,"longitude":dlng}, "direct_distance": _distance_m(route_origin,{"latitude":dlat,"longitude":dlng}), "location_source":origin.get("source"), "location_mode":origin.get("mode"), "started_at": now_iso()}
    _navigations[device_id] = result
    if route["mode"] == "amap":
        _record_guard_event(device_id, "navigation_started", "开始导航", str(destination.get("name") or ""))
    await _publish(device_id, {"type": "navigation_update", "navigation": result})
    return result


@router.get("/api/v1/navigation/status")
async def navigation_status(device_id: str = "hat-001", authorization: str | None = Header(default=None)):
    username = require_session(authorization); device_id = _device(device_id); binding = _binding_or_owner(username, device_id, location=True)
    nav = _navigations.get(device_id)
    if nav and binding["owner_username"].casefold() != username.casefold() and (
        (nav.get("route") and nav["route"].get("mode") != "amap") or nav.get("location_mode") == "demo"
    ):
        return {"device_id": device_id, "status": "IDLE"}
    if nav and _expired(_latest_location(device_id)) and nav["status"] in ("ROUTE_READY", "NAVIGATING"):
        return {**nav, "status":"PAUSED", "detail":"位置数据已过期，导航进度暂停"}
    return nav or {"device_id":device_id,"status":"IDLE"}


@router.post("/api/v1/navigation/cancel")
async def cancel_navigation(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization); device_id = _device(payload.get("device_id", "hat-001")); _binding_or_owner(username, device_id)
    previous = _navigations.get(device_id)
    result = {"status": "CANCELLED", "device_id": device_id, "updated_at": now_iso()}
    _navigations[device_id] = result
    if previous and previous.get("route", {}).get("mode") == "amap":
        _record_guard_event(device_id, "navigation_cancelled", "导航已取消")
    await _publish(device_id, {"type": "navigation_update", "navigation": result})
    return result


@router.get("/api/v1/video/mjpeg")
async def mjpeg_video(request: Request, device_id: str = "hat-001", authorization: str | None = Header(default=None)):
    # Native <img> requests use the short-lived same-origin qh_token cookie;
    # API clients may use the normal Authorization header.
    token = authorization
    if not token and request is not None:
        cookie = request.cookies.get("qh_token")
        token = "Bearer " + cookie if cookie else None
    username = require_session(token); device_id = _device(device_id); _binding_or_owner(username, device_id, camera=True)
    _record_camera_view(username, device_id)
    async def stream():
        last_seq = None
        last_auth_check = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            if time.monotonic() - last_auth_check >= 1:
                last_auth_check = time.monotonic()
                current = session_username(token.partition(" ")[2] if token else None)
                if current != username:
                    break
                try:
                    _binding_or_owner(username, device_id, camera=True)
                except HTTPException:
                    break
                _record_camera_view(username, device_id)
            frame = camera_frames.get(device_id)
            if frame is None:
                with db() as connection:
                    owner = connection.execute("SELECT owner_username FROM device_family_bindings WHERE device_id=? ORDER BY id LIMIT 1", (device_id,)).fetchone()
                if owner and owner["owner_username"].casefold() == username.casefold():
                    frame = camera_frames.get("local_camera")
            if frame and frame.seq != last_seq:
                last_seq = frame.seq
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame.image)).encode() + b"\r\n\r\n" + frame.image + b"\r\n"
            await asyncio.sleep(0.08)
    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.websocket("/ws/v1/guard/{device_id}")
async def guard_ws(ws: WebSocket, device_id: str):
    await ws.accept()
    try:
        auth_message = await asyncio.wait_for(ws.receive_json(), timeout=5)
        token = auth_message.get("token") if auth_message.get("type") == "authenticate" else None
        username = session_username(token)
        if not username:
            await ws.close(code=4401); return
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        await ws.close(code=4401); return
    try:
        _binding_or_owner(username, _device(device_id), location=True)
    except HTTPException:
        await ws.close(code=4403); return
    _guard_clients.setdefault(device_id, set()).add(ws)
    with db() as connection:
        owner = connection.execute("SELECT owner_username FROM device_family_bindings WHERE device_id=? ORDER BY id LIMIT 1", (device_id,)).fetchone()
    _guard_sessions[ws] = (username.casefold(), device_id, bool(owner and owner["owner_username"].casefold() == username.casefold()), token)
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        _guard_clients.get(device_id, set()).discard(ws)
        _guard_sessions.pop(ws, None)


@router.post("/api/v1/map/assistant")
async def map_assistant(payload: dict, authorization: str | None = Header(default=None)):
    username = require_session(authorization)
    device_id = _device(payload.get("device_id", "hat-001"))
    _binding_or_owner(username, device_id, location=True)
    text = str(payload.get("text") or "").strip()[:500]
    if not text:
        raise HTTPException(status_code=422, detail="请先输入或说出地图问题")
    normalized = text.replace(" ", "")
    location = _latest_location(device_id)
    if any(word in normalized for word in ("取消导航", "停止导航", "结束导航")):
        nav = await cancel_navigation({"device_id": device_id}, authorization)
        return {"tool": "cancel_navigation", "result": nav, "reply": "已取消当前路线。"}
    if _expired(location):
        return {"tool": "get_current_location", "result": {"available": False}, "reply": "当前位置暂不可用，收到有效位置后再试。"}

    # Deterministic intent path for nearby/current-location questions. These
    # queries must use the real location-aware endpoint directly; an optional
    # LLM must not reinterpret "附近医院" as a generic place-text search.
    if any(word in normalized for word in ("我在哪", "当前位置", "现在在哪", "当前街区")):
        result = await reverse_geocode({"device_id": device_id, "latitude": location["latitude"], "longitude": location["longitude"]}, authorization)
        return {"tool": "get_current_location", "result": {"location": location, "address": result}, "reply": f"{result['address']}。"}
    if "附近" in normalized or any(word in normalized for word in ("医院", "公交站", "便利店", "药店", "厕所")):
        keyword = next((word for word in ("医院", "公交站", "地铁站", "便利店", "药店", "公共厕所", "厕所") if word in normalized), "医院")
        result = await nearby({"device_id": device_id, "keyword": keyword}, authorization)
        if not result["results"]:
            return {"tool": "search_nearby", "result": result, "reply": f"附近没有找到{keyword}。"}
        first = result["results"][0]
        prefix = "演示数据：" if result["mode"] == "mock" else ""
        return {"tool": "search_nearby", "result": result, "reply": f"{prefix}最近的{keyword}是{first['name']}，约 {first['distance']} 米，{first['address']}。"}

    if settings.openai_base_url and settings.openai_api_key and settings.openai_model:
        import json
        tools = [
            {"type":"function","function":{"name":"get_current_location","description":"检查共享位置是否有效","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
            {"type":"function","function":{"name":"reverse_geocode","description":"查询当前位置的实际街区地址","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
            {"type":"function","function":{"name":"search_nearby","description":"搜索当前位置附近地点。先取到真实搜索结果后才能介绍地点","parameters":{"type":"object","properties":{"keyword":{"type":"string"},"radius":{"type":"integer","minimum":100,"maximum":50000}},"required":["keyword"],"additionalProperties":False}}},
            {"type":"function","function":{"name":"search_places","description":"按地点名称和可选城市搜索真实目的地，结果返回后才可以规划路线","parameters":{"type":"object","properties":{"keyword":{"type":"string"},"city":{"type":"string"}},"required":["keyword"],"additionalProperties":False}}},
            {"type":"function","function":{"name":"plan_walking_route","description":"用户明确要求前往某个附近地点时，为前一次搜索结果规划步行路线","parameters":{"type":"object","properties":{"poi_index":{"type":"integer","minimum":0,"maximum":9}},"required":["poi_index"],"additionalProperties":False}}},
            {"type":"function","function":{"name":"cancel_navigation","description":"取消当前路线导航","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        ]
        prompt = ("你是启明辅行地图助手。地点、地址、距离和路线都必须来自工具；不得编造或猜测。没有有效位置时如实告知。只有用户明确要求前往/导航/路线才生成导航。普通步行路线不是视障安全路线。" +
                  ("当前仅有模拟位置，回答必须标为演示。" if location and location.get("mode") == "demo" else "") +
                  ("当前位置数据已过期。" if _expired(location) else ""))
        messages = [{"role":"system","content":prompt},{"role":"user","content":text}]
        candidates = []
        calls_done = []
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                for _ in range(3):
                    response = await client.post(settings.openai_base_url.rstrip("/") + "/chat/completions", headers={"Authorization":"Bearer " + settings.openai_api_key}, json={"model":settings.openai_model,"messages":messages,"tools":tools,"tool_choice":"auto","temperature":0.2})
                    response.raise_for_status()
                    assistant = response.json()["choices"][0]["message"]
                    tool_calls = assistant.get("tool_calls") or []
                    if not tool_calls:
                        if calls_done:
                            last = next((call.get("client_result", call["result"]) for call in reversed(calls_done) if call["tool"] in ("plan_walking_route","search_nearby","search_places")), None)
                            return {"tool":"llm_tool_call","result":last,"calls":calls_done,"reply":assistant.get("content") or "已完成地图查询。"}
                        break
                    messages.append(assistant)
                    for call in tool_calls[:4]:
                        name = call.get("function", {}).get("name")
                        client_output = None
                        try: args = json.loads(call.get("function", {}).get("arguments") or "{}")
                        except (ValueError, TypeError): args = {}
                        if name == "get_current_location":
                            output = {"available":not _expired(location),"source":_source_label(location)}
                        elif name == "reverse_geocode" and not _expired(location):
                            output = await reverse_geocode({"device_id":device_id,"latitude":location["latitude"],"longitude":location["longitude"]}, authorization)
                        elif name in ("search_nearby", "search_places"):
                            if name == "search_nearby":
                                output = await nearby({"device_id":device_id,"keyword":args.get("keyword"),"radius":args.get("radius",3000)}, authorization)
                            else:
                                output = await search_places({"device_id":device_id,"keyword":args.get("keyword"),"city":args.get("city")}, authorization)
                            candidates = output.get("results", [])
                            client_output = output
                            output = {"mode":output.get("mode"),"keyword":output.get("keyword"),"results":[{key:item.get(key) for key in ("name","address","distance","type")} for item in candidates]}
                        elif name == "plan_walking_route" and any(word in normalized for word in ("带我去","导航到","路线","怎么走")):
                            index = args.get("poi_index", 0)
                            if not isinstance(index,int) or isinstance(index,bool) or index < 0 or index >= len(candidates):
                                output = {"error":"没有可用的附近地点，请先搜索"}
                            else:
                                poi = candidates[index]
                                output = await walking_route({"device_id":device_id,"destination":{"latitude":poi["latitude"],"longitude":poi["longitude"]}}, authorization)
                                client_output = output
                        elif name == "cancel_navigation" and any(word in normalized for word in ("取消","停止","结束")):
                            output = await cancel_navigation({"device_id":device_id}, authorization)
                        else:
                            output = {"error":"工具不可用或用户没有提出此操作"}
                        calls_done.append({"tool":name,"result":output,**({"client_result":client_output} if client_output is not None else {})})
                        messages.append({"role":"tool","tool_call_id":call.get("id"),"content":json.dumps(output,ensure_ascii=False)})
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    if any(word in normalized for word in ("取消导航", "停止导航", "结束导航")):
        nav = await cancel_navigation({"device_id": device_id}, authorization)
        return {"tool": "cancel_navigation", "result": nav, "reply": "已取消当前路线。"}
    return {"tool": None, "result": None, "reply": "可以问我当前位置，或附近的医院、公交站、便利店。"}
