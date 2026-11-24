"""HTTP status and dedicated hat-speech WebSocket routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .auth import require_session
from .database import db, now_iso
from .services import runtime
from .tts_delivery import tts_delivery
from .tts_synthesis import TtsSynthesisError
import tempfile
import asyncio
import secrets
import time


router = APIRouter()
_browser_tickets: dict[str, tuple[str, float]] = {}


def _new_browser_ticket(text: str) -> str:
    now = time.monotonic()
    for key, (_value, expires) in list(_browser_tickets.items()):
        if expires <= now:
            _browser_tickets.pop(key, None)
    while len(_browser_tickets) >= 256:
        _browser_tickets.pop(next(iter(_browser_tickets)))
    ticket = secrets.token_urlsafe(32)
    _browser_tickets[ticket] = (text, now + 60)
    return ticket


@router.post("/api/tts/synthesize-browser")
async def tts_synthesize_browser(payload: dict, authorization: str | None = Header(default=None)):
    """Return the same cached Edge TTS WAV used for computer or hat playback."""
    require_session(authorization)
    if not runtime.speech_enabled:
        raise HTTPException(status_code=409, detail="语音播报已关闭")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="没有可朗读的回复")
    try:
        phrase = await tts_delivery.ensure_phrase(text)
        await asyncio.to_thread(tts_delivery.library.load_pcm, phrase)
        audio = await asyncio.to_thread(phrase.path.read_bytes)
    except TtsSynthesisError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=503, detail=f"语音文件不可用：{error}") from None
    return Response(audio, media_type="audio/wav", headers={
        "Cache-Control": "no-store", "X-TTS-Voice": phrase.voice,
        "X-TTS-Sample-Rate": str(phrase.sample_rate), "X-TTS-Phrase-Id": phrase.phrase_id,
    })


@router.post("/api/tts/browser-ticket")
async def tts_browser_ticket(payload: dict, authorization: str | None = Header(default=None)):
    """Create a short-lived, one-use playback URL so HTMLAudio can stream MP3."""
    require_session(authorization)
    if not runtime.speech_enabled:
        raise HTTPException(status_code=409, detail="语音播报已关闭")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="没有可朗读的回复")
    if len(text) > 500:
        raise HTTPException(status_code=422, detail="语音文本超过 500 字")
    try:
        cached = tts_delivery.library.get_by_text(text) is not None
    except ValueError as error:
        raise HTTPException(status_code=503, detail="本地语音库不可用") from error
    synthesizer = tts_delivery.synthesizer
    if not cached and (synthesizer is None or not getattr(synthesizer, "available", True)):
        raise HTTPException(status_code=503, detail="Edge TTS 或 FFmpeg 当前不可用")
    ticket = _new_browser_ticket(text)
    return {"url": f"/api/tts/browser-stream/{ticket}", "cached": cached}


@router.get("/api/tts/browser-stream/{ticket}")
async def tts_browser_stream(ticket: str):
    """Stream cached WAV or Edge TTS MP3; dynamically generated MP3 also warms PCM cache."""
    entry = _browser_tickets.pop(ticket, None)
    if entry is None or entry[1] <= time.monotonic():
        raise HTTPException(status_code=404, detail="语音播放链接已过期，请重新请求")
    text = entry[0]
    try:
        phrase = tts_delivery.library.get_by_text(text)
        if phrase is not None:
            await asyncio.to_thread(tts_delivery.library.load_pcm, phrase)
            audio = await asyncio.to_thread(phrase.path.read_bytes)
            return Response(audio, media_type="audio/wav", headers={
                "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            })
    except ValueError as error:
        raise HTTPException(status_code=503, detail="本地语音库不可用") from error

    return StreamingResponse(
        tts_delivery.stream_browser_mp3_and_cache(text),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/api/tts/library")
async def tts_library_status(authorization: str | None = Header(default=None)):
    require_session(authorization)
    library = tts_delivery.library
    try:
        summary = library.summary()
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {
        **summary,
        "device_connections": sorted(tts_delivery.peers),
        "device_outputs": {device_id: peer.output_ready for device_id, peer in tts_delivery.peers.items()},
        "realtime_synthesis_available": bool(tts_delivery.synthesizer and tts_delivery.synthesizer.available),
    }


@router.post("/api/tts/speak")
async def tts_speak(payload: dict, authorization: str | None = Header(default=None)):
    require_session(authorization)
    if not runtime.speech_enabled:
        raise HTTPException(status_code=409, detail="帽端语音播报已关闭")
    device_id = str(payload.get("device_id") or "")
    phrase_id = str(payload.get("phrase_id") or "")
    text = str(payload.get("text") or "")
    priority = payload.get("priority", 1)
    result = (
        tts_delivery.queue_phrase(device_id, phrase_id, priority=priority)
        if phrase_id else tts_delivery.queue_text(device_id, text, priority=priority)
    )
    status = result.get("status")
    if status == "device_offline":
        raise HTTPException(status_code=409, detail="帽端 TTS 通道未连接")
    if status == "output_unconfigured":
        raise HTTPException(status_code=409, detail="帽端 I2S 输出未配置或未就绪")
    if status == "sample_rate_mismatch":
        raise HTTPException(status_code=422, detail="音频采样率与帽端 16 kHz 播放配置不一致")
    if status == "asset_missing":
        raise HTTPException(status_code=404, detail="本地语音库没有该 phrase_id")
    if status == "queue_full":
        raise HTTPException(status_code=429, detail="帽端播报队列已满")
    if status in {"synthesizing", "queued"}:
        return result
    if status == "synthesis_queue_full":
        raise HTTPException(status_code=429, detail="语音合成队列已满")
    if status == "synthesis_unavailable":
        raise HTTPException(status_code=503, detail="实时合成需要安装 Edge TTS 和 FFmpeg")
    if status == "text_too_long":
        raise HTTPException(status_code=422, detail="语音文本超过 500 字")
    if status == "library_error":
        raise HTTPException(status_code=503, detail="语音合成或本地语音库暂不可用")
    if status == "synthesis_failed":
        raise HTTPException(status_code=502, detail="Edge TTS 合成失败")
    if status == "empty_text":
        raise HTTPException(status_code=422, detail="请提供 phrase_id 或非空 text")
    raise HTTPException(status_code=422, detail="phrase_id、text 或 priority 无效")


@router.post("/api/tts/stop")
async def tts_stop(payload: dict, authorization: str | None = Header(default=None)):
    require_session(authorization)
    device_id = str(payload.get("device_id") or "")
    result = tts_delivery.cancel_device(device_id)
    if result.get("status") == "device_offline":
        raise HTTPException(status_code=409, detail="帽端 TTS 通道未连接")
    return result


@router.websocket("/ws/tts")
async def tts_ws(websocket: WebSocket):
    device_id = websocket.query_params.get("device_id", "")
    try:
        peer = await tts_delivery.connect(device_id, websocket)
    except ValueError:
        await websocket.close(code=1008)
        return
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                response = await tts_delivery.receive(peer, message["bytes"])
            elif message.get("text") is not None:
                response = await tts_delivery.receive(peer, message["text"])
            else:
                response = {"type": "tts_error", "code": "EMPTY_MESSAGE"}
            if response:
                if response.get("type") == "tts_playback_status" and response.get("task_id"):
                    await tts_delivery.publish_task_status(response)
                else:
                    await tts_delivery._send_json(peer, response)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        await tts_delivery.disconnect(peer)
