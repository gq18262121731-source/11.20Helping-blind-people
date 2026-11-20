import asyncio, json, struct, io
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Header, Response
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db, db, now_iso
from .services import runtime, dialogue
from .ocr import MAX_IMAGE_BYTES, OCRServiceError, recognize_text
from .overview import overview
from .tablet import router as tablet_router
from .raw_camera import router as raw_camera_router
from .family import router as family_router
from .camera_stream import camera_frames, capture_latest_frame
from .audio_protocol import AudioProtocolError
from .voice_session import VoiceSessionManager
from .intent import Intent
from .asr import ASRError, asr
from .auth import create_captcha, create_session, register_user, require_session, revoke_session, session_username, verify_captcha

app=FastAPI(title="启明智能陪护管理平台")
app.include_router(tablet_router)
app.include_router(raw_camera_router)
app.include_router(family_router)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])


async def _voice_intent_handler(match, session):
    """Execute the small, deterministic first-version voice command set."""
    device_id = session.start.device_id
    if match.intent is Intent.STOP:
        return {"intent": match.intent.value, "reply": "已停止当前任务。", "request_type": "stop"}
    if match.intent is Intent.ENV_QUERY:
        reply, kind = await dialogue(device_id, match.text)
        return {"intent": match.intent.value, "reply": reply, "request_type": kind}
    if match.intent is Intent.OCR_AGAIN:
        previous = getattr(runtime, "last_ocr", {}).get(device_id)
        return {"intent": match.intent.value, "reply": previous or "还没有上一条识别结果。", "request_type": "ocr_again"}
    if match.intent in (Intent.OCR_READ, Intent.CAMERA_CAPTURE):
        # A device-scoped voice task must never silently read another device's
        # frame.  ``helmet`` is only used by local sessions without a device id.
        source = device_id or "helmet"
        frame = capture_latest_frame(source, 1000, device_id=device_id or None)
        if not frame:
            return {"intent": match.intent.value, "reply": "帽端摄像头离线或画面已过期，请稍后再试。", "request_type": "ocr_read"}
        if match.intent is Intent.CAMERA_CAPTURE:
            return {"intent": match.intent.value, "reply": "已获取当前画面。", "request_type": "camera_capture", "image_seq": frame.seq}
        try:
            result = await recognize_text(frame.image)
        except OCRServiceError as error:
            return {"intent": match.intent.value, "reply": error.detail, "request_type": "ocr_read", "ocr_status": "failed"}
        runtime.last_ocr[device_id] = result["text"]
        return {"intent": match.intent.value, "reply": result["text"], "request_type": "ocr_read", "ocr_status": "ok", "ocr": result}
    return {"intent": match.intent.value, "reply": "我没有听懂，请重新表达。", "request_type": "unknown"}


voice_sessions = VoiceSessionManager(intent_handler=_voice_intent_handler,
                                     timeout=settings.audio_session_timeout_seconds)

@app.on_event("startup")
async def startup():
    init_db()
    with db() as c:
        saved={r["key"]:json.loads(r["value_json"]) for r in c.execute("SELECT key,value_json FROM system_config WHERE scope='server'").fetchall()}
    if "confidence" in saved: settings.yolo_confidence=float(saved["confidence"])
    if "left_ratio" in saved: settings.left_ratio=float(saved["left_ratio"])
    if "right_ratio" in saved: settings.right_ratio=float(saved["right_ratio"])
    DEFAULT_CONFIG.update(saved)
    runtime.speech_enabled = bool(DEFAULT_CONFIG.get("speech_enabled", True))
    runtime.last_ocr = {}
    await runtime.load_model(); asyncio.create_task(runtime.detect_loop())

@app.get("/")
async def index(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/dashboard.html")
async def dashboard(request: Request):
    if not session_username(request.cookies.get("qh_token")):
        return RedirectResponse("/", status_code=303)
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "index.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/login.html")
async def login_page(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/login.js")
async def login_script(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.js")

@app.get("/camera-speech.js")
async def camera_speech_script(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "camera-speech.js", headers={"Cache-Control":"no-store"})

@app.get("/camera-monitor.js")
async def camera_monitor_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "camera-monitor.js", headers={"Cache-Control": "no-store"})

@app.get("/ocr-reader.js")
async def ocr_reader_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "ocr-reader.js", headers={"Cache-Control":"no-store"})

@app.get("/overview.js")
async def overview_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "overview.js", headers={"Cache-Control":"no-store"})

@app.get("/overview.css")
async def overview_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "overview.css", headers={"Cache-Control":"no-store"})

@app.get("/api/overview")
async def system_overview(tz: str = "Asia/Shanghai", authorization: str | None = Header(default=None)):
    require_session(authorization)
    try:
        return await overview(tz)
    except ValueError:
        raise HTTPException(status_code=422, detail="无效的时区") from None

@app.post("/api/ocr/read")
async def read_camera_text(request: Request, authorization: str | None = Header(default=None)):
    require_session(authorization)
    image = bytearray()
    async for chunk in request.stream():
        image.extend(chunk)
        if len(image) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="图片不能超过 6 MB")
    try:
        result = await recognize_text(bytes(image))
    except OCRServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    return {**result, "timestamp": now_iso()}

@app.get("/login-config.js")
async def login_config(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login-config.js")

@app.get("/assets/qiming-hat-hero.png")
async def login_hero(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "assets" / "qiming-hat-hero.png")

@app.get("/api/auth/captcha")
async def auth_captcha(): return create_captcha()

@app.get("/api/auth/session")
async def auth_session(authorization: str | None = Header(default=None)):
    require_session(authorization)
    return {"authenticated": True}

@app.post("/api/auth/logout")
async def auth_logout(response: Response, authorization: str | None = Header(default=None)):
    revoke_session(authorization)
    response.delete_cookie("qh_token", path="/")
    return {"logged_out": True}

@app.post("/api/auth/login")
async def auth_login(payload: dict):
    verify_captcha(str(payload.get("captcha_id", "")), str(payload.get("captcha", "")))
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入账号和密码")
    return create_session(username, password, bool(payload.get("remember")))

@app.post("/api/auth/register")
async def auth_register(payload: dict):
    verify_captcha(str(payload.get("captcha_id", "")), str(payload.get("captcha", "")))
    username = str(payload.get("username", "")).strip()
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    if not payload.get("terms"):
        raise HTTPException(status_code=400, detail="请先阅读并同意用户服务协议和隐私政策")
    if not 4 <= len(username) <= 20 or not username.isascii() or not username.isalnum():
        raise HTTPException(status_code=400, detail="请输入4-20位字母或数字")
    if len(email) > 200 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="请输入有效邮箱地址")
    if len(password) < 6 or len(password) > 20 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password) or not password.isalnum():
        raise HTTPException(status_code=400, detail="密码需为6-20位，且包含字母和数字")
    if payload.get("confirm_password") != password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    return register_user(username, email, password)

@app.get("/api/health")
async def health(): return {"ok":True,"model_status":"simulation" if settings.simulation_mode else ("ready" if runtime.model else "unavailable"),"model_error":runtime.model_error,"devices":list(runtime.devices)}

@app.post("/api/local-camera/frame")
async def local_camera_frame(request: Request, seq: int = 0):
    image = await request.body()
    if not image or len(image) > 2_000_000 or not image.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=400, detail="需要小于 2 MB 的 JPEG 图像帧")
    camera_frames.publish("local_camera", seq, image)
    if runtime.image_queue.full():
        try: runtime.image_queue.get_nowait()
        except asyncio.QueueEmpty: pass
    await runtime.image_queue.put(("local_camera", seq, image))
    return {"accepted": True, "device_id": "local_camera", "seq": seq}

@app.get("/api/obstacles")
async def obstacles(limit:int=100):
    with db() as c: rows=c.execute("SELECT * FROM obstacle_logs ORDER BY id DESC LIMIT ?",(min(limit,500),)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/voices")
async def voices(limit:int=100):
    with db() as c: rows=c.execute("SELECT * FROM voice_logs ORDER BY id DESC LIMIT ?",(min(limit,500),)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/devices")
async def devices():
    with db() as c: rows=c.execute("SELECT * FROM device_info ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]

DEFAULT_CONFIG={"obstacle_enabled":True,"voice_enabled":True,"camera_enabled":True,"speech_enabled":True,"confidence":settings.yolo_confidence,"left_ratio":settings.left_ratio,"right_ratio":settings.right_ratio,"alert_cooldown_ms":2000,"heartbeat_interval_s":10,"image_interval_ms":250}

@app.get("/api/config")
async def get_config():
    with db() as c: rows=c.execute("SELECT key,value_json FROM system_config WHERE scope='server'").fetchall()
    out=dict(DEFAULT_CONFIG)
    for r in rows:
        try: out[r["key"]]=json.loads(r["value_json"])
        except Exception: pass
    return out

@app.put("/api/config")
async def put_config(payload: dict):
    allowed=set(DEFAULT_CONFIG); clean={k:v for k,v in payload.items() if k in allowed}
    if "speech_enabled" in clean and not isinstance(clean["speech_enabled"], bool):
        raise HTTPException(status_code=422,detail="speech_enabled 必须是布尔值")
    if "confidence" in clean:
        try: clean["confidence"]=float(clean["confidence"])
        except (TypeError,ValueError): raise HTTPException(status_code=422,detail="confidence 必须是 0 到 1 的数值")
        if not 0.05 <= clean["confidence"] <= 0.95: raise HTTPException(status_code=422,detail="confidence 范围必须为 0.05 到 0.95")
        settings.yolo_confidence=clean["confidence"]
    for key in ("left_ratio","right_ratio"):
        if key in clean:
            try: clean[key]=float(clean[key])
            except (TypeError,ValueError): raise HTTPException(status_code=422,detail=f"{key} 必须是数值")
            if not 0.1 <= clean[key] <= 0.9: raise HTTPException(status_code=422,detail=f"{key} 范围必须为 0.1 到 0.9")
    next_left=clean.get("left_ratio",settings.left_ratio); next_right=clean.get("right_ratio",settings.right_ratio)
    if next_left >= next_right: raise HTTPException(status_code=422,detail="left_ratio 必须小于 right_ratio")
    settings.left_ratio=next_left
    settings.right_ratio=next_right
    if "speech_enabled" in clean:
        runtime.speech_enabled=clean["speech_enabled"]
    with db() as c:
        for k,v in clean.items(): c.execute("INSERT INTO system_config(key,value_json,scope,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(k,json.dumps(v),"server",now_iso()))
    return {**DEFAULT_CONFIG,**clean}

@app.get("/api/export/obstacles.xlsx")
async def export_obstacles():
    import pandas as pd
    with db() as c: rows=[dict(x) for x in c.execute("SELECT * FROM obstacle_logs ORDER BY id DESC").fetchall()]
    buf=io.BytesIO(); pd.DataFrame(rows).to_excel(buf,index=False); buf.seek(0)
    return StreamingResponse(buf,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=obstacle_logs.xlsx"})

@app.get("/api/tablet/config")
async def tablet_get_config(authorization: str | None = Header(default=None)):
    require_session(authorization)
    return await get_config()

@app.put("/api/tablet/config")
async def tablet_put_config(payload: dict, authorization: str | None = Header(default=None)):
    require_session(authorization)
    # Only expose parameters already applied by the detector, not unwired switches.
    allowed = {"confidence", "left_ratio", "right_ratio"}
    if not payload or set(payload) - allowed:
        raise HTTPException(status_code=422, detail="平板端只支持检测置信度和方位比例")
    await put_config(payload)
    return await get_config()

@app.post("/api/simulate/voice")
async def simulate_voice(payload: dict):
    """无硬件时测试 ASR 之后的分类、环境查询和日志链路。"""
    device_id = payload.get("device_id", "simulator_001")
    text = payload.get("text", "")
    reply, kind = await dialogue(device_id, text)
    with db() as c:
        c.execute("INSERT INTO voice_logs(device_id,occurred_at,user_text,reply_text,request_type,asr_status,tts_status,created_at) VALUES(?,?,?,?,?,?,?,?)", (device_id, now_iso(), text, reply, kind, "simulation", "simulation", now_iso()))
    result = {"type":"voice_reply", "device_id":device_id, "text":reply, "request_type":kind, "asr_status":"simulation", "tts_status":"simulation", "timestamp":now_iso()}
    await runtime.broadcast(result)
    return result


@app.post("/api/simulate/audio")
async def simulate_audio(request: Request):
    """Recognize a local 16kHz WAV before any ESP32 microphone is connected."""
    data = await request.body()
    if len(data) > 20_000_000:
        raise HTTPException(status_code=413, detail="WAV 文件不能超过 20 MB")
    try:
        result = await asr.recognize_wav(data)
    except ASRError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {"type": "asr_result", "text": result.text, "confidence": result.confidence,
            "asr_status": "ok", "source": "wav"}

@app.websocket("/ws/web")
async def web_ws(ws:WebSocket):
    await ws.accept(); runtime.web_clients.add(ws)
    try:
        while True:
            msg=await ws.receive_json()
            if msg.get("type")=="device_command": await runtime.send_device(msg.get("device_id"),msg)
            elif msg.get("type")=="config_update": await runtime.broadcast(msg)
    except WebSocketDisconnect: runtime.web_clients.discard(ws)


@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket):
    """Receive session JSON plus binary PCM, independent from camera frames."""
    await ws.accept()
    session_id = None
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    ws.receive(), settings.audio_session_timeout_seconds if session_id else None
                )
            except asyncio.TimeoutError as error:
                raise AudioProtocolError("音频会话超时") from error
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(code=message.get("code", 1000))
            if message.get("text"):
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError as error:
                    raise AudioProtocolError("音频控制消息不是有效 JSON") from error
                kind = payload.get("type")
                if kind == "audio_start":
                    session = voice_sessions.start(payload)
                    session_id = session.start.session_id
                    await ws.send_json({"type": "audio_started", "session_id": session_id,
                                        "state": "RECORDING"})
                elif kind == "audio_end":
                    if payload.get("session_id") != session_id:
                        raise AudioProtocolError("session_id 不匹配")
                    result = await voice_sessions.finish(session_id)
                    result.update({"type": "voice_result", "state": "COMPLETED" if result.get("asr_status") == "ok" else "LISTENING"})
                    await ws.send_json(result)
                    await runtime.broadcast(result)
                    session_id = None
                elif kind == "audio_cancel":
                    voice_sessions.cancel(str(payload.get("session_id", session_id)))
                    await ws.send_json({"type": "audio_cancelled", "state": "LISTENING"})
                    session_id = None
                else:
                    raise AudioProtocolError("未知音频控制消息")
            elif message.get("bytes") is not None:
                if not session_id:
                    raise AudioProtocolError("请先发送 audio_start")
                voice_sessions.append(session_id, message["bytes"])
    except AudioProtocolError as error:
        await ws.send_json({"type": "error", "code": "BAD_AUDIO", "detail": str(error)})
    except (WebSocketDisconnect, RuntimeError):
        if session_id:
            voice_sessions.cancel(session_id)

@app.websocket("/ws/device")
async def device_ws(ws:WebSocket):
    await ws.accept(); device_id=None
    try:
        while True:
            data=await ws.receive()
            if data.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(code=data.get("code", 1000))
            if data.get("text"):
                msg=json.loads(data["text"]); device_id=msg.get("device_id",device_id)
                if msg.get("type")=="device_online":
                    runtime.devices[device_id]=ws
                    with db() as c: c.execute("INSERT INTO device_info(device_id,name,firmware_version,online,last_heartbeat,camera_ok,mic_ok,left_motor_ok,right_motor_ok,speaker_ok,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET online=1,firmware_version=excluded.firmware_version,last_heartbeat=excluded.last_heartbeat,updated_at=excluded.updated_at",(device_id,device_id,msg.get("firmware_version"),1,now_iso(),1,1,1,1,1,now_iso()))
                    await runtime.broadcast({"type":"device_status","device_id":device_id,"online":True,"timestamp":now_iso()})
                elif msg.get("type")=="heartbeat" and device_id:
                    with db() as c: c.execute("UPDATE device_info SET online=1,last_heartbeat=?,updated_at=? WHERE device_id=?",(now_iso(),now_iso(),device_id))
                elif msg.get("type")=="asr_result" and device_id:
                    reply,kind=await dialogue(device_id,msg.get("text", "")); out={"type":"voice_reply","device_id":device_id,"text":reply,"timestamp":now_iso()}; await ws.send_json(out); await runtime.broadcast({**msg,"device_id":device_id,"reply":reply,"request_type":kind})
            elif data.get("bytes") and device_id:
                raw=data["bytes"]
                if raw.startswith(b"QH") and len(raw)>=26:
                    hlen=struct.unpack_from("<H",raw,4)[0]; seq=struct.unpack_from("<I",raw,6)[0]; payload_len=struct.unpack_from("<I",raw,22)[0]; payload=raw[hlen:hlen+payload_len]
                    if raw[3]==1:
                        camera_frames.publish(device_id, seq, payload)
                        if runtime.image_queue.full(): runtime.image_queue.get_nowait()
                        await runtime.image_queue.put((device_id,seq,payload))
    except WebSocketDisconnect:
        if device_id:
            camera_frames.remove(device_id)
            runtime.devices.pop(device_id,None)
            await runtime.broadcast({"type":"device_status","device_id":device_id,"online":False,"timestamp":now_iso()})

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=settings.port)

