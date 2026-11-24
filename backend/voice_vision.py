"""Shared intent-to-vision task execution for web, simulation and device audio."""
from __future__ import annotations

import asyncio
import time

from .intent import Intent, classifier
from .ocr import OCRServiceError, recognize_text
from .vision_interpretation import VLMServiceError, compose_reply, describe_scene


async def execute_task(
    text: str,
    *,
    image: bytes | None = None,
    detection_result: dict | None = None,
    source_mode: str = "software",
    source_id: str | None = None,
    device_id: str | None = None,
    frame_seq: int | None = None,
    previous_ocr: str = "",
    asr_status: str = "not_used",
    tts_target: str = "none",
    intent_match=None,
) -> dict:
    started = time.perf_counter()
    match = intent_match or classifier.classify(text)
    intent_ms = round((time.perf_counter() - started) * 1000, 1)
    normalized_mode = source_mode if source_mode in {"hardware", "software", "simulation"} else "software"
    result = {
        "intent": match.intent.value,
        "intent_confidence": match.confidence,
        "source_mode": normalized_mode,
        "source_id": source_id,
        "device_id": device_id,
        "frame_seq": frame_seq,
        "asr_status": asr_status,
        "intent_status": "ok",
        "yolo_status": (detection_result or {}).get("model_status", "unavailable"),
        "vlm_status": "not_needed",
        "reply_status": "ok",
        "tts_target": tts_target,
        "tts_status": "not_requested",
        "stage_latency_ms": {"intent": intent_ms, **({"yolo": detection_result["latency_ms"]} if detection_result and "latency_ms" in detection_result else {})},
        "detection_snapshot": list((detection_result or {}).get("objects") or []),
        "ocr_result": None,
        "ocr_model": None,
        "vlm_scene_description": "",
        "vlm_model": None,
        "response_basis": [],
        "error_code": None,
    }
    objects = result["detection_snapshot"]

    if match.intent is Intent.STOP:
        result.update(final_reply="已停止当前任务。", response_basis=["intent"])
        result["tts_target"] = "none"
        return _finish(result, started)
    if match.intent is Intent.OCR_AGAIN:
        result.update(
            final_reply=previous_ocr or "还没有上一条识别结果。",
            response_basis=["previous_ocr"] if previous_ocr else ["intent"],
            ocr_status="ok" if previous_ocr else "empty",
        )
        return _finish(result, started)
    if match.intent is Intent.CAMERA_CAPTURE:
        if not image:
            return _failure(result, "没有可用画面，请先连接设备或启动摄像头", "NO_FRAME", started)
        result.update(final_reply="已获取当前画面。", response_basis=["intent"])
        return _finish(result, started)
    if match.intent is Intent.OCR_READ:
        if not image:
            return _failure(result, "没有可用画面，请先连接设备或启动摄像头", "NO_FRAME", started)
        ocr_started = time.perf_counter()
        try:
            ocr = await recognize_text(image)
        except OCRServiceError as error:
            result["stage_latency_ms"]["ocr"] = round((time.perf_counter() - ocr_started) * 1000, 1)
            result["ocr_status"] = "failed"
            result["error_code"] = "OCR_ERROR"
            result["reply_status"] = "failed"
            result["final_reply"] = error.detail
            return _finish(result, started)
        result["stage_latency_ms"]["ocr"] = round((time.perf_counter() - ocr_started) * 1000, 1)
        result["ocr_status"] = "ok" if ocr.get("has_text") else "empty"
        result["ocr_result"] = ocr
        result["ocr_model"] = ocr.get("model")
        result["final_reply"] = ocr.get("text", "未识别到清晰文字")
        result["response_basis"] = ["ocr"]
        return _finish(result, started)
    if match.intent in (Intent.ENV_QUERY, Intent.SCENE_DESCRIBE):
        scene = ""
        scene_error = ""
        if image:
            vlm_started = time.perf_counter()
            try:
                scene_result = await describe_scene(image, text)
                scene = scene_result["scene_description"]
                result["vlm_model"] = scene_result.get("model")
                result["stage_latency_ms"]["vlm"] = scene_result.get("latency_ms")
                result["vlm_status"] = "ok"
            except VLMServiceError as error:
                result["stage_latency_ms"]["vlm"] = round((time.perf_counter() - vlm_started) * 1000, 1)
                result["vlm_status"] = "unavailable" if error.code == "VLM_UNAVAILABLE" else "failed"
                result["error_code"] = error.code
                scene_error = error.detail
        else:
            result["vlm_status"] = "no_frame"
            result["error_code"] = "NO_FRAME"
            scene_error = "当前来源没有可用画面"
        result["vlm_scene_description"] = scene
        composed = compose_reply(
            match.intent, text, objects, scene, scene_error=scene_error,
            detection_status=result["yolo_status"],
        )
        result["final_reply"] = composed["reply"]
        result["response_basis"] = composed["response_basis"]
        if result["vlm_status"] != "ok":
            result["reply_status"] = "partial" if result["yolo_status"] in {"ready", "simulation"} else "failed"
        return _finish(result, started)
    result["intent_status"] = "unknown"
    result["reply_status"] = "failed"
    result["error_code"] = "UNKNOWN_INTENT"
    result["final_reply"] = "我没有听懂，请说再读一遍、读一下文字、前面有什么或描述画面。"
    result["response_basis"] = ["intent"]
    return _finish(result, started)


def _failure(result: dict, reply: str, code: str, started: float) -> dict:
    result.update(final_reply=reply, reply_status="failed", error_code=code, response_basis=[])
    if result["intent"] == Intent.OCR_READ.value:
        result["ocr_status"] = "unavailable"
    return _finish(result, started)


def _finish(result: dict, started: float) -> dict:
    result["stage_latency_ms"]["total"] = round((time.perf_counter() - started) * 1000, 1)
    result["reply_text"] = result.get("final_reply", "")
    return result


async def detect_image_once(image: bytes, source_id: str = "voice_task") -> dict:
    """Run the existing YOLO model once on an uploaded/current task frame."""
    import cv2
    import numpy as np

    from .config import settings
    from .services import runtime

    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return {"objects": [], "model_status": "invalid_image", "error": "图片无法解码"}
    if not runtime.model or not runtime.detector_enabled:
        state = "disabled" if not runtime.detector_enabled else "unavailable"
        return {"objects": [], "model_status": state, "error": runtime.model_error}
    started = time.perf_counter()
    try:
        async with runtime.inference_lock:
            predictions = await asyncio.to_thread(
                runtime.model.predict,
                decoded,
                conf=settings.yolo_confidence,
                imgsz=settings.yolo_image_size,
                half=runtime.yolo_half and runtime.gpu_available,
                verbose=False,
            )
        try:
            rows = [] if predictions is None else list(predictions)
        except (IndexError, TypeError, ValueError, AttributeError) as error:
            # A malformed empty Results iterator should not turn a valid
            # camera frame into a service error.
            rows = []
        boxes = getattr(rows[0], "boxes", None) if rows else None
        names = runtime.model.names
        objects = []
        for box in boxes or []:
            try:
                x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
                confidence = float(box.conf[0])
                class_id = int(box.cls[0])
                if isinstance(names, dict):
                    class_name = names.get(class_id, str(class_id))
                elif 0 <= class_id < len(names):
                    class_name = names[class_id]
                else:
                    class_name = str(class_id)
                objects.append({
                    "class": str(class_name),
                    "confidence": round(confidence, 3),
                    "bbox": [x1, y1, x2, y2],
                    "direction": runtime.direction((x1 + x2) / 2, decoded.shape[1]),
                })
            except (IndexError, KeyError, TypeError, ValueError, AttributeError):
                continue
        return {
            "objects": objects,
            "model_status": "ready",
            "frame_width": int(decoded.shape[1]),
            "frame_height": int(decoded.shape[0]),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "source_id": source_id,
        }
    except Exception as error:
        return {"objects": [], "model_status": "error", "error": str(error),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "source_id": source_id}
