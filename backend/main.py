import asyncio, json, struct, io
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Header, Response
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db, db, now_iso
from .services import runtime, dialogue
from .vision import VisionConfigError, load_vision_config
from .ocr import MAX_IMAGE_BYTES, OCRServiceError, recognize_text
from .overview import overview
from .tablet import router as tablet_router
from .raw_camera import router as raw_camera_router
from .family import router as family_router
from .frontend_preview import router as frontend_preview_router
from .remote_guard import router as remote_guard_router
from .tts_routes import router as tts_router
from .camera_stream import camera_frames, capture_latest_frame
from .audio_protocol import AudioProtocolError
from .voice_session import VoiceSessionManager
from .intent import Intent
from .asr import ASRError, asr
from .auth import create_captcha, create_session, register_user, require_session, revoke_session, session_username, verify_captcha
from .voice_vision import detect_image_once, execute_task
from .intent import classifier
from .intent.intent_classifier import IntentMatch
from .tts_delivery import tts_delivery

app=FastAPI(title="启明智能陪护管理平台")
app.include_router(tablet_router)
app.include_router(raw_camera_router)
app.include_router(family_router)
app.include_router(frontend_preview_router)
app.include_router(remote_guard_router)
app.include_router(tts_router)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])


async def _publish_tts_task_status(event: dict):
    task_id = event.get("task_id")
    request_id = event.get("request_id")
    if task_id is None:
        return
    with db() as connection:
        if event["status"] == "queued" and request_id is not None:
            cursor = connection.execute(
                "UPDATE multimodal_tasks SET tts_request_id=?,tts_status='queued' WHERE id=? AND tts_target='device'",
                (request_id, task_id),
            )
        elif request_id is None:
            cursor = connection.execute(
                "UPDATE multimodal_tasks SET tts_status=? WHERE id=? AND tts_target='device' AND tts_request_id IS NULL AND tts_status='synthesizing'",
                (event["status"], task_id),
            )
        else:
            cursor = connection.execute(
                "UPDATE multimodal_tasks SET tts_status=? WHERE id=? AND tts_request_id=?",
                (event["status"], task_id, request_id),
            )
        if cursor.rowcount == 0:
            return
        task = connection.execute(
            "SELECT voice_interaction_id FROM multimodal_tasks WHERE id=?", (task_id,),
        ).fetchone()
        connection.execute("UPDATE voice_logs SET tts_status=? WHERE id=?", (event["status"], task["voice_interaction_id"]))
    event["timestamp"] = now_iso()
    await runtime.broadcast(event)


tts_delivery.on_task_status = _publish_tts_task_status


def _request_type(intent: str) -> str:
    return {
        "OCR_READ": "ocr_read", "OCR_AGAIN": "ocr_again", "CAMERA_CAPTURE": "camera_capture",
        "STOP": "stop", "ENV_QUERY": "environment_query", "SCENE_DESCRIBE": "scene_describe",
        "UNKNOWN": "unknown",
    }.get(str(intent), str(intent).lower())


def _source_mode(source_id: str | None, requested: str | None = None) -> str:
    source = str(source_id or "")
    if source.startswith("simulator_"):
        return "simulation"
    if source and source not in {"local_camera", "upload", "browser"}:
        return "hardware"
    return "software"


def _frame_context(source_id: str | None, frame_seq: int | None = None):
    """Resolve one source and one matching detector result for a task."""
    source = source_id or "local_camera"
    detection = None
    if frame_seq is not None:
        detection = runtime.detection_history.get((source, int(frame_seq)))
    if detection is None:
        latest = runtime.latest.get(source)
        if latest and (frame_seq is None or latest.get("result", {}).get("image_seq") == frame_seq):
            detection = latest.get("result")
    frame = capture_latest_frame(source, 1500)
    if frame_seq is not None and frame is not None and frame.seq != int(frame_seq):
        frame = None
    if frame is None:
        detection = None
    elif detection is not None and detection.get("image_seq") != frame.seq:
        detection = None
    return source, frame, detection


def _store_task(user_text: str, result: dict) -> int:
    """Persist the compatibility voice row and the detailed multimodal row."""
    occurred_at = now_iso()
    request_type = _request_type(result.get("intent"))
    tts_status = result.get("tts_status") or "not_requested"
    with db() as c:
        voice_cursor = c.execute(
            "INSERT INTO voice_logs(device_id,occurred_at,user_text,reply_text,request_type,asr_status,tts_status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (result.get("device_id") or result.get("source_id"), occurred_at, user_text,
             result.get("final_reply", ""), request_type, result.get("asr_status"), tts_status, occurred_at),
        )
        voice_interaction_id = voice_cursor.lastrowid
        task_cursor = c.execute(
            """INSERT INTO multimodal_tasks(
                voice_interaction_id,device_id,source_id,source_mode,frame_seq,intent,intent_confidence,
                detection_snapshot,ocr_result,vlm_scene_description,ocr_model,vlm_model,final_reply,
                response_basis,asr_status,intent_status,yolo_status,vlm_status,reply_status,tts_target,
                tts_status,stage_latency_ms,error_code,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (voice_interaction_id, result.get("device_id"), result.get("source_id"), result.get("source_mode"),
             result.get("frame_seq"), result.get("intent"), result.get("intent_confidence", 0),
             json.dumps(result.get("detection_snapshot") or [], ensure_ascii=False),
             json.dumps(result.get("ocr_result"), ensure_ascii=False) if result.get("ocr_result") is not None else None,
             result.get("vlm_scene_description") or None, result.get("ocr_model"), result.get("vlm_model"),
             result.get("final_reply", ""), json.dumps(result.get("response_basis") or [], ensure_ascii=False),
             result.get("asr_status"), result.get("intent_status"), result.get("yolo_status"),
             result.get("vlm_status"), result.get("reply_status"), result.get("tts_target"), tts_status,
             json.dumps(result.get("stage_latency_ms") or {}, ensure_ascii=False), result.get("error_code"), occurred_at),
        )
        return int(task_cursor.lastrowid)


def _queue_device_speech(task_id: int, device_id: str | None, text: str) -> dict:
    if not device_id or not text or not runtime.speech_enabled:
        status = "disabled" if not runtime.speech_enabled else "device_offline"
        response = {"status": status}
    else:
        response = tts_delivery.queue_text(device_id, text, priority=1, task_id=task_id)
    with db() as connection:
        connection.execute(
            "UPDATE multimodal_tasks SET tts_target='device',tts_status=?,tts_request_id=? WHERE id=?",
            (response.get("status", "failed"), response.get("request_id"), task_id),
        )
        connection.execute(
            "UPDATE voice_logs SET tts_status=? WHERE id=(SELECT voice_interaction_id FROM multimodal_tasks WHERE id=?)",
            (response.get("status", "failed"), task_id),
        )
    return response


async def _voice_intent_handler(match, session):
    """Route device ASR through the same task processor as web text."""
    source_id = getattr(session.start, "source_id", "") or session.start.device_id
    source_mode = getattr(session.start, "source_mode", None) or _source_mode(source_id)
    tts_target = getattr(session.start, "tts_target", "device")
    frame_seq = getattr(session.start, "frame_seq", None)
    source, frame, detection = _frame_context(source_id, frame_seq)
    if frame and match.intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE) and (
        not detection or detection.get("image_seq") != frame.seq
    ):
        detection = await detect_image_once(frame.image, source_id=source)
        detection["image_seq"] = frame.seq
    mode = source_mode
    device_id = source if tts_target == "device" else None
    result = await execute_task(
        match.text,
        image=frame.image if frame else None,
        detection_result=detection,
        source_mode=mode,
        source_id=source,
        device_id=device_id,
        frame_seq=frame.seq if frame else (detection or {}).get("image_seq"),
        previous_ocr=getattr(runtime, "last_ocr", {}).get(device_id or source, ""),
        asr_status="ok",
        tts_target=tts_target,
        intent_match=match,
    )
    if result.get("intent") == Intent.OCR_READ.value and result.get("ocr_result", {}).get("text"):
        runtime.last_ocr[device_id or source] = result["ocr_result"]["text"]
    if match.intent is Intent.STOP and device_id:
        result["tts_status"] = tts_delivery.cancel_device(device_id).get("status", "not_requested")
    task_id = _store_task(match.text, result)
    if match.intent is not Intent.STOP and device_id and tts_target == "device":
        result["tts_status"] = _queue_device_speech(task_id, device_id, result.get("final_reply", ""))["status"]
    result.update(type="voice_reply", task_id=task_id, request_type=_request_type(result.get("intent")), reply=result.get("final_reply", ""))
    return result


voice_sessions = VoiceSessionManager(intent_handler=_voice_intent_handler,
                                     timeout=settings.audio_session_timeout_seconds)

@app.on_event("startup")
async def startup():
    try:
        load_vision_config()
    except VisionConfigError as error:
        raise RuntimeError(str(error)) from error
    init_db()
    with db() as c:
        saved={r["key"]:json.loads(r["value_json"]) for r in c.execute("SELECT key,value_json FROM system_config WHERE scope='server'").fetchall()}
    if "confidence" in saved: settings.yolo_confidence=float(saved["confidence"])
    if "left_ratio" in saved: settings.left_ratio=float(saved["left_ratio"])
    if "right_ratio" in saved: settings.right_ratio=float(saved["right_ratio"])
    DEFAULT_CONFIG.update(saved)
    runtime.speech_enabled = bool(DEFAULT_CONFIG.get("speech_enabled", True))
    runtime.alerts_enabled = bool(DEFAULT_CONFIG.get("obstacle_enabled", True))
    runtime.alert_tts_enabled = bool(DEFAULT_CONFIG.get("voice_enabled", True))
    runtime.alert_scheduler.cooldown_seconds = max(
        2.0, float(DEFAULT_CONFIG.get("alert_cooldown_ms", 2000)) / 1000.0,
    )
    runtime.last_ocr = {}
    await runtime.load_model()
    await runtime.load_depth_model()
    runtime.depth_task = asyncio.create_task(runtime.depth_loop())
    asyncio.create_task(runtime.detect_loop())

@app.get("/")
async def index(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/favicon.ico")
async def favicon(): return Response(status_code=204)

@app.get("/dashboard.html")
async def dashboard(request: Request):
    if not session_username(request.cookies.get("qh_token")):
        return RedirectResponse("/", status_code=303)
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "index.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/login.html")
async def login_page(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.html", headers={"Cache-Control":"no-store, no-cache, must-revalidate"})

@app.get("/login.js")
async def login_script(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login.js", headers={"Cache-Control":"no-store"})

@app.get("/camera-speech.js")
async def camera_speech_script(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "camera-speech.js", headers={"Cache-Control":"no-store"})

@app.get("/camera-monitor.js")
async def camera_monitor_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "camera-monitor.js", headers={"Cache-Control": "no-store"})

@app.get("/ocr-reader.js")
async def ocr_reader_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "ocr-reader.js", headers={"Cache-Control":"no-store"})

@app.get("/voice-vision.js")
async def voice_vision_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "voice-vision.js", headers={"Cache-Control":"no-store"})

@app.get("/voice-input.js")
async def voice_input_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "voice-input.js", headers={"Cache-Control":"no-store"})

@app.get("/voice-input-worklet.js")
async def voice_input_worklet_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "voice-input-worklet.js", headers={"Cache-Control":"no-store"})

@app.get("/voice-vision.css")
async def voice_vision_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "voice-vision.css", headers={"Cache-Control":"no-store"})

@app.get("/overview.js")
async def overview_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "overview.js", headers={"Cache-Control":"no-store"})

@app.get("/console-ui.js")
async def console_ui_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "console-ui.js", headers={"Cache-Control":"no-store"})

@app.get("/family-assist.js")
async def family_assist_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "family-assist.js", headers={"Cache-Control": "no-store"})

@app.get("/family-assist.css")
async def family_assist_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "family-assist.css", headers={"Cache-Control": "no-store"})

@app.get("/remote-guard.js")
async def remote_guard_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "remote-guard.js", headers={"Cache-Control": "no-store"})

@app.get("/tts-client.js")
async def tts_client_script():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "tts-client.js", headers={"Cache-Control": "no-store"})

@app.get("/remote-guard.css")
async def remote_guard_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "remote-guard.css", headers={"Cache-Control": "no-store"})

@app.get("/overview.css")
async def overview_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "overview.css", headers={"Cache-Control":"no-store"})

@app.get("/overview-theme.css")
async def overview_theme_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "overview-theme.css", headers={"Cache-Control":"no-store"})

@app.get("/ocr-page.css")
async def ocr_page_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "ocr-page.css", headers={"Cache-Control": "no-store"})

@app.get("/brand.css")
async def brand_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "brand.css", headers={"Cache-Control":"no-store"})

@app.get("/login-theme.css")
async def login_theme_styles():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login-theme.css", headers={"Cache-Control":"no-store"})

@app.get("/api/overview")
async def system_overview(tz: str = "Asia/Shanghai", authorization: str | None = Header(default=None)):
    require_session(authorization)
    try:
        return await overview(tz)
    except ValueError:
        raise HTTPException(status_code=422, detail="无效的时区") from None


@app.get("/api/voice-vision/capabilities")
async def voice_vision_capabilities(authorization: str | None = Header(default=None)):
    require_session(authorization)
    from .asr import asr as asr_service
    from .ocr import _rapid_engine

    device_ready = bool(tts_delivery.peers)
    try:
        device_tts_library = "available" if tts_delivery.library.available else "unavailable"
    except ValueError:
        device_tts_library = "invalid"
    device_tts_synthesis = bool(tts_delivery.synthesizer and tts_delivery.synthesizer.available)
    device_tts_output = (
        "unavailable" if any(peer.output_ready is False for peer in tts_delivery.peers.values())
        else "initializing" if any(peer.output_ready is None for peer in tts_delivery.peers.values())
        else "ready" if device_ready else "unavailable"
    )
    vlm_configured = bool(settings.openai_base_url and settings.openai_model)
    import importlib.util
    rapid_installed = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    ocr_ready = _rapid_engine is not None or rapid_installed or vlm_configured
    return {
        "capabilities": [
            {"name": "intent", "status": "ready", "provider": "IntentClassifier", "detail": "确定性本地意图识别"},
            {"name": "yolo", "status": "ready" if runtime.model else "unavailable", "provider": "YOLO", "detail": runtime.model_error},
            {"name": "ocr", "status": "configured" if ocr_ready else "unavailable", "provider": settings.ocr_engine, "model": "RapidOCR" if _rapid_engine else None},
            {"name": "vlm", "status": "configured" if vlm_configured else "unavailable", "provider": "OpenAI-compatible", "model": settings.openai_model or None},
            {"name": "asr", "status": "configured" if asr_service.available and importlib.util.find_spec("sherpa_onnx") else "unavailable", "provider": "Zipformer Large INT8 · CPU"},
            {"name": "browser_tts", "status": "client_capability", "provider": "Edge TTS + FFmpeg", "detail": "电脑端回复先转为 16 kHz 单声道 PCM WAV 再播放"},
            {"name": "device_tts", "status": "unavailable" if not device_ready else "output_unconfigured" if device_tts_output == "unavailable" else "initializing" if device_tts_output == "initializing" else "connected" if device_tts_synthesis or device_tts_library == "available" else "synthesis_unavailable", "provider": "Edge TTS + local cache", "library": device_tts_library, "synthesis": "ready" if device_tts_synthesis else "unavailable", "output": device_tts_output},
        ],
        "checked_at": now_iso(),
    }


@app.post("/api/voice-vision/tasks")
async def create_voice_vision_task(request: Request, authorization: str | None = Header(default=None)):
    require_session(authorization)
    content_type = request.headers.get("content-type", "").lower()
    image = None
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="任务参数不是有效 JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="任务参数必须为 JSON 对象")
        text = str(payload.get("text", ""))[:500]
        source_id = str(payload.get("source_id") or "local_camera")
        requested_mode = payload.get("source_mode")
        requested_seq = payload.get("frame_seq")
        requested_intent = payload.get("intent")
        asr_status = str(payload.get("asr_status") or "not_used")
        tts_target = str(payload.get("tts_target") or "browser")
    else:
        if content_type.split(";", 1)[0] not in {"image/jpeg", "image/png"}:
            raise HTTPException(status_code=415, detail="请提交 JSON、JPEG 或 PNG")
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="图片不能超过 6 MB")
        image = bytes(chunks)
        if not image:
            raise HTTPException(status_code=400, detail="图片不能为空")
        text = request.query_params.get("text", "描述当前画面")[:500]
        source_id = request.query_params.get("source_id", "upload")
        requested_mode = request.query_params.get("source_mode", "software")
        requested_seq = request.query_params.get("frame_seq")
        requested_intent = request.query_params.get("intent")
        asr_status = "not_used"
        tts_target = request.query_params.get("tts_target", "browser")
    from .camera_stream import SOURCE_ID
    if not SOURCE_ID.fullmatch(source_id):
        raise HTTPException(status_code=422, detail="画面来源编号无效")
    if requested_mode is not None and requested_mode not in {"hardware", "software", "simulation"}:
        raise HTTPException(status_code=422, detail="来源模式无效")
    if tts_target not in {"none", "browser", "device", "simulation"}:
        raise HTTPException(status_code=422, detail="朗读目标无效")
    actual_mode = _source_mode(source_id)
    if requested_mode is not None and requested_mode != actual_mode:
        raise HTTPException(status_code=422, detail="来源模式与当前画面来源不匹配")
    if actual_mode == "hardware" and source_id not in runtime.devices:
        raise HTTPException(status_code=409, detail="所选帽端设备当前未连接，请刷新设备状态")
    if not text.strip() and not requested_intent:
        raise HTTPException(status_code=422, detail="请输入问题或选择任务意图")
    if requested_seq is not None:
        try:
            requested_seq = int(requested_seq)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="frame_seq 必须为整数") from None
    if requested_intent:
        try:
            forced_intent = Intent(str(requested_intent))
        except ValueError:
            raise HTTPException(status_code=422, detail="不支持的任务意图") from None
        confidence = 1.0
        match = IntentMatch(forced_intent, text or forced_intent.value, confidence)
    else:
        match = classifier.classify(text)
    frame = None
    detection = None
    frame_seq = None
    actual_mode = _source_mode(source_id)
    if image:
        try:
            from .ocr import image_mime
            image_mime(image)
        except OCRServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None
        if actual_mode == "hardware":
            frame = capture_latest_frame(source_id, 1500, device_id=source_id)
            if frame is None or requested_seq is None or frame.seq != requested_seq or frame.image != image:
                raise HTTPException(status_code=409, detail="帽端原始帧已更新，请重新拍摄后再分析")
            frame_seq = frame.seq
            if match.intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE):
                detection = runtime.detection_history.get((source_id, frame.seq))
                if not detection or detection.get("image_seq") != frame.seq:
                    detection = await detect_image_once(frame.image, source_id=source_id)
                    detection["image_seq"] = frame.seq
            else:
                detection = {"objects": [], "model_status": "not_requested"}
        elif match.intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE):
            detection = await detect_image_once(image, source_id=source_id)
        else:
            detection = {"objects": [], "model_status": "not_requested"}
    else:
        source_id, frame, detection = _frame_context(source_id, requested_seq)
        if requested_seq is not None and frame is None:
            raise HTTPException(status_code=409, detail="所选画面帧已过期，请重新拍摄")
        if frame:
            frame_seq = frame.seq
        if frame and match.intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE) and (
            not detection or detection.get("image_seq") != frame.seq
        ):
            detection = await detect_image_once(frame.image, source_id=source_id)
            detection["image_seq"] = frame.seq
    if requested_mode in {"hardware", "software", "simulation"} and requested_mode != actual_mode:
        raise HTTPException(status_code=422, detail="来源模式与当前画面来源不匹配")
    if actual_mode == "hardware" and source_id not in runtime.devices:
        raise HTTPException(status_code=409, detail="所选帽端设备当前未连接，请刷新设备状态")
    device_id = source_id if actual_mode == "hardware" else None
    result = await execute_task(
        text or match.text,
        image=image or (frame.image if frame else None),
        detection_result=detection,
        source_mode=actual_mode,
        source_id=source_id,
        device_id=device_id,
        frame_seq=frame_seq,
        asr_status=asr_status,
        tts_target=tts_target,
        previous_ocr=runtime.last_ocr.get(device_id or source_id, ""),
        intent_match=match,
    )
    if result.get("intent") == Intent.OCR_READ.value and result.get("ocr_result", {}).get("text"):
        runtime.last_ocr[device_id or source_id] = result["ocr_result"]["text"]
    if match.intent is Intent.STOP and device_id:
        result["tts_status"] = tts_delivery.cancel_device(device_id).get("status", "not_requested")
    task_id = _store_task(text or match.text, result)
    if match.intent is not Intent.STOP and tts_target == "device":
        result["tts_status"] = _queue_device_speech(task_id, device_id, result.get("final_reply", ""))["status"]
    result.update(task_id=task_id, type="voice_vision_task_result", timestamp=now_iso())
    result["user_text"] = text or match.text
    await runtime.broadcast({key: value for key, value in result.items() if key != "ocr_result"})
    return result


@app.get("/api/voice-vision/tasks")
async def list_voice_vision_tasks(
    limit: int = 100,
    source_mode: str | None = None,
    intent: str | None = None,
    status: str | None = None,
    authorization: str | None = Header(default=None),
):
    require_session(authorization)
    clauses, values = [], []
    if source_mode in {"hardware", "software", "simulation"}:
        clauses.append("source_mode=?")
        values.append(source_mode)
    if intent:
        clauses.append("intent=?")
        values.append(intent)
    if status:
        clauses.append("reply_status=?")
        values.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with db() as connection:
        rows = connection.execute(
            "SELECT *,(SELECT user_text FROM voice_logs WHERE voice_logs.id=multimodal_tasks.voice_interaction_id) AS user_text FROM multimodal_tasks" + where + " ORDER BY id DESC LIMIT ?",
            (*values, min(max(limit, 1), 500)),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("detection_snapshot", "ocr_result", "response_basis", "stage_latency_ms"):
            try:
                item[key] = json.loads(item[key]) if item.get(key) else None
            except (TypeError, json.JSONDecodeError):
                item[key] = None
        result.append(item)
    return result


@app.patch("/api/voice-vision/tasks/{task_id}/tts")
async def update_voice_vision_tts(task_id: int, payload: dict, authorization: str | None = Header(default=None)):
    require_session(authorization)
    status = str(payload.get("status", ""))
    target = str(payload.get("target", "browser"))
    if status not in {"queued", "pending", "speaking", "completed", "failed", "unavailable", "disabled", "interrupted", "not_requested", "device_offline", "asset_missing", "library_error", "queue_full"}:
        raise HTTPException(status_code=422, detail="不支持的朗读状态")
    if target not in {"browser", "device", "simulation"}:
        raise HTTPException(status_code=422, detail="不支持的朗读目标")
    if target == "device":
        raise HTTPException(status_code=422, detail="帽端播放状态仅接受设备回执，请使用任务播报接口")
    with db() as connection:
        cursor = connection.execute(
            "UPDATE multimodal_tasks SET tts_target=?,tts_status=? WHERE id=?",
            (target, status, task_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="未找到该任务")
        connection.execute(
            "UPDATE voice_logs SET tts_status=? WHERE id=(SELECT voice_interaction_id FROM multimodal_tasks WHERE id=?)",
            (status, task_id),
        )
    return {"task_id": task_id, "tts_target": target, "tts_status": status, "updated_at": now_iso()}


@app.post("/api/voice-vision/tasks/{task_id}/speak")
async def speak_voice_vision_task(task_id: int, authorization: str | None = Header(default=None)):
    require_session(authorization)
    with db() as connection:
        task = connection.execute(
            "SELECT device_id,source_mode,final_reply FROM multimodal_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="未找到该任务")
    if task["source_mode"] != "hardware" or not task["device_id"]:
        raise HTTPException(status_code=409, detail="当前任务不是帽端实机来源")
    if not task["final_reply"]:
        raise HTTPException(status_code=422, detail="任务没有可朗读的回复")
    result = _queue_device_speech(task_id, task["device_id"], task["final_reply"])
    status = result.get("status")
    if status == "device_offline":
        raise HTTPException(status_code=409, detail="帽端 TTS 通道未连接")
    if status == "asset_missing":
        raise HTTPException(status_code=404, detail="本地语音库没有该回复的音频资源")
    if status == "synthesis_queue_full":
        raise HTTPException(status_code=429, detail="语音合成队列已满")
    if status == "synthesis_unavailable":
        raise HTTPException(status_code=503, detail="实时合成需要 Edge TTS 与 FFmpeg")
    if status == "queue_full":
        raise HTTPException(status_code=429, detail="帽端播报队列已满")
    if status == "synthesis_unavailable":
        raise HTTPException(status_code=503, detail="未配置 Edge TTS 与 FFmpeg")
    if status not in {"queued", "synthesizing"}:
        raise HTTPException(status_code=503, detail="帽端语音资源暂不可用")
    result.update(task_id=task_id, target="device", timestamp=now_iso())
    return result

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
async def login_config(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "login-config.js", headers={"Cache-Control":"no-store"})

@app.get("/assets/qiming-hat-hero.png")
async def login_hero(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "assets" / "qiming-hat-hero.png", headers={"Cache-Control":"no-store"})

@app.get("/assets/qiming-hat-outline.png")
async def login_hat_outline(): return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "assets" / "qiming-hat-outline.png", headers={"Cache-Control":"no-store"})

@app.get("/assets/qiming-assistive-user.webp")
async def login_user_portrait():
    return FileResponse(
        Path(__file__).resolve().parent.parent / "frontend" / "assets" / "qiming-assistive-user.webp",
        media_type="image/webp",
        headers={"Cache-Control":"no-store"},
    )

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
async def health(): return {"ok":True,"model_status":"simulation" if settings.simulation_mode else "disabled" if not runtime.detector_enabled else ("ready" if runtime.model else "unavailable"),"model_error":runtime.model_error,"depth_status":"disabled" if not settings.depth_enabled else ("ready" if runtime.depth_error is None and runtime.depth_estimator.model else "unavailable"),"depth_error":runtime.depth_error,"devices":list(runtime.devices)}

@app.get("/api/spatial/status")
async def spatial_status():
    """Expose truthful depth/risk runtime state for the monitoring workbench."""
    return {
        "depth": {
            "enabled": settings.depth_enabled,
            "status": "disabled" if not settings.depth_enabled else ("ready" if runtime.depth_estimator.model else "unavailable"),
            "error": runtime.depth_error,
            "device": runtime.depth_estimator.device,
            "precision": runtime.depth_estimator.precision,
            "model": "Depth-Anything-V2-Small",
        },
        "gpu": {"available": runtime.gpu_available, "name": runtime.gpu_name,
                "total_memory_mb": runtime.gpu_total_memory_mb},
        "risk": {"enabled": True, "score_unit": "internal_score", "score_is_probability": False},
        "alert_delivery_errors": runtime.alert_scheduler.errors[-10:],
        "performance": runtime.performance_snapshot(),
    }

@app.post("/api/local-camera/frame")
async def local_camera_frame(request: Request, seq: int = 0):
    image = await request.body()
    if not image or len(image) > 2_000_000 or not image.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=400, detail="需要小于 2 MB 的 JPEG 图像帧")
    camera_frames.publish("local_camera", seq, image)
    runtime.enqueue_frame("local_camera", seq, image)
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
        if not 0 <= clean["confidence"] <= 1: raise HTTPException(status_code=422,detail="confidence 范围必须为 0 到 1")
    if "alert_cooldown_ms" in clean:
        value = clean["alert_cooldown_ms"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 9007199254740991:
            raise HTTPException(status_code=422,detail="预警冷却时间必须是大于 0 的整数")
    for key in ("left_ratio","right_ratio"):
        if key in clean:
            try: clean[key]=float(clean[key])
            except (TypeError,ValueError): raise HTTPException(status_code=422,detail=f"{key} 必须是数值")
            if not 0.1 <= clean[key] <= 0.9: raise HTTPException(status_code=422,detail=f"{key} 范围必须为 0.1 到 0.9")
    next_left=clean.get("left_ratio",settings.left_ratio); next_right=clean.get("right_ratio",settings.right_ratio)
    if next_left >= next_right: raise HTTPException(status_code=422,detail="left_ratio 必须小于 right_ratio")
    if "confidence" in clean: settings.yolo_confidence=clean["confidence"]
    settings.left_ratio=next_left
    settings.right_ratio=next_right
    if "speech_enabled" in clean:
        runtime.speech_enabled=clean["speech_enabled"]
        runtime.speech_announced.clear()
        if not clean["speech_enabled"]:
            for device_id in tuple(tts_delivery.peers):
                tts_delivery.cancel_device(device_id)
    if "obstacle_enabled" in clean:
        runtime.alerts_enabled = bool(clean["obstacle_enabled"])
    if "voice_enabled" in clean:
        runtime.alert_tts_enabled = bool(clean["voice_enabled"])
    if "alert_cooldown_ms" in clean:
        runtime.alert_scheduler.cooldown_seconds = max(2.0, clean["alert_cooldown_ms"] / 1000.0)
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
async def simulate_voice(payload: dict, authorization: str | None = Header(default=None)):
    """Compatibility route into the shared intent and multimodal task pipeline."""
    require_session(authorization)
    device_id = str(payload.get("device_id", "simulator_001"))
    text = str(payload.get("text", ""))[:500]
    if not text.strip():
        raise HTTPException(status_code=422, detail="请输入测试问题")
    source, frame, detection = _frame_context(device_id)
    if frame and (not detection or detection.get("image_seq") != frame.seq) and classifier.classify(text).intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE):
        detection = await detect_image_once(frame.image, source_id=source)
        detection["image_seq"] = frame.seq
    result = await execute_task(
        text,
        image=frame.image if frame else None,
        detection_result=detection,
        source_mode="simulation",
        source_id=device_id,
        device_id=device_id,
        frame_seq=frame.seq if frame else (detection or {}).get("image_seq"),
        asr_status="simulation",
        tts_target="simulation",
    )
    result["tts_status"] = "simulation"
    task_id = _store_task(text, result)
    result.update(type="voice_reply", task_id=task_id, device_id=device_id,
                  text=result.get("final_reply", ""), request_type=_request_type(result.get("intent")),
                  asr_status="simulation", tts_status="simulation", timestamp=now_iso())
    await runtime.broadcast({key: value for key, value in result.items() if key != "ocr_result"})
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
                    input_source = str(payload.get("input_source") or "hardware")
                    if input_source == "computer" and not session_username(ws.cookies.get("qh_token")):
                        await ws.send_json({"type": "error", "code": "AUTH_REQUIRED", "detail": "电脑麦克风需要先登录"})
                        continue
                    if not asr.available:
                        await ws.send_json({"type": "error", "code": "ASR_UNAVAILABLE", "detail": "Zipformer 模型文件不可用，请检查本地 ASR 配置"})
                        continue
                    session = voice_sessions.start(payload)
                    session_id = session.start.session_id
                    await ws.send_json({"type": "audio_started", "session_id": session_id,
                                        "state": "RECORDING", "input_source": session.start.input_source,
                                        "source_id": session.start.source_id, "source_mode": session.start.source_mode})
                elif kind == "audio_end":
                    if payload.get("session_id") != session_id:
                        raise AudioProtocolError("session_id 不匹配")
                    result = await voice_sessions.finish(session_id)
                    result.update({"type": "voice_result", "state": "COMPLETED" if result.get("asr_status") == "ok" else "LISTENING"})
                    await ws.send_json(result)
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
                session = voice_sessions.append(session_id, message["bytes"])
                partial = voice_sessions.partial_text.get(session_id, "")
                if partial:
                    await ws.send_json({"type": "asr_partial", "session_id": session_id, "text": partial,
                                        "state": "RECORDING"})
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
                    from types import SimpleNamespace
                    match = classifier.classify(msg.get("text", ""))
                    session = SimpleNamespace(start=SimpleNamespace(device_id=device_id))
                    result = await _voice_intent_handler(match, session)
                    await ws.send_json({**result, "type": "voice_reply", "device_id": device_id,
                                        "text": result.get("reply", ""), "timestamp": now_iso()})
                    await runtime.broadcast({**result, "type": "asr_result", "device_id": device_id,
                                             "text": msg.get("text", ""), "timestamp": now_iso()})
            elif data.get("bytes") and device_id:
                raw=data["bytes"]
                if raw.startswith(b"QH") and len(raw)>=26:
                    hlen=struct.unpack_from("<H",raw,4)[0]; seq=struct.unpack_from("<I",raw,6)[0]; payload_len=struct.unpack_from("<I",raw,22)[0]; payload=raw[hlen:hlen+payload_len]
                    if raw[3]==1:
                        camera_frames.publish(device_id, seq, payload)
                        runtime.enqueue_frame(device_id, seq, payload)
    except WebSocketDisconnect:
        if device_id:
            camera_frames.remove(device_id)
            runtime.devices.pop(device_id,None)
            await runtime.broadcast({"type":"device_status","device_id":device_id,"online":False,"timestamp":now_iso()})

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=settings.port)

