import asyncio, base64, json, os, time
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timezone
import cv2, numpy as np
from .config import settings
from .database import db, now_iso, log_system
from .camera_stream import camera_frames
from .tts_delivery import tts_delivery
from .vision import (
    AlertController, AlertLevel, ApproachDetector, TrackerAdapter,
    AlertEvent, AlertScheduler, DepthAnythingEstimator, DepthModelError,
    SpatialFusion, SpatialRiskEngine, load_vision_config,
)
from .vision.model_setup import ensure_depth_assets

OBJECT_NAMES_ZH = {
    "person": "行人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
    "airplane": "飞机", "bus": "公交车", "train": "火车", "truck": "卡车", "boat": "船",
    "traffic light": "红绿灯", "fire hydrant": "消防栓", "stop sign": "停车标志",
    "parking meter": "停车计时器", "bench": "长椅", "bird": "鸟", "cat": "猫",
    "dog": "狗", "horse": "马", "sheep": "羊", "cow": "牛", "elephant": "大象",
    "bear": "熊", "zebra": "斑马", "giraffe": "长颈鹿", "backpack": "背包",
    "umbrella": "雨伞", "handbag": "手提包", "tie": "领带", "suitcase": "行李箱",
    "frisbee": "飞盘", "skis": "滑雪板", "snowboard": "单板滑雪板", "sports ball": "球",
    "kite": "风筝", "baseball bat": "棒球棒", "baseball glove": "棒球手套",
    "skateboard": "滑板", "surfboard": "冲浪板", "tennis racket": "网球拍",
    "bottle": "瓶子", "wine glass": "高脚杯", "cup": "水杯", "fork": "叉子",
    "knife": "刀", "spoon": "勺子", "bowl": "碗", "banana": "香蕉", "apple": "苹果",
    "sandwich": "三明治", "orange": "橙子", "broccoli": "西兰花", "carrot": "胡萝卜",
    "hot dog": "热狗", "pizza": "披萨", "donut": "甜甜圈", "cake": "蛋糕",
    "chair": "椅子", "couch": "沙发", "potted plant": "盆栽", "bed": "床",
    "dining table": "餐桌", "toilet": "马桶", "tv": "电视", "laptop": "笔记本电脑",
    "mouse": "鼠标", "remote": "遥控器", "keyboard": "键盘", "cell phone": "手机",
    "microwave": "微波炉", "oven": "烤箱", "toaster": "烤面包机", "sink": "水槽",
    "refrigerator": "冰箱", "book": "书", "clock": "时钟", "vase": "花瓶",
    "scissors": "剪刀", "teddy bear": "玩具熊", "hair drier": "吹风机", "toothbrush": "牙刷",
}
OBJECT_SPEECH_CONFIRM_FRAMES = 2
OBJECT_SPEECH_LOST_SECONDS = 3.0
MAX_OBJECTS_PER_ANNOUNCEMENT = 6

class Runtime:
    def __init__(self):
        self.v2_config = load_vision_config(settings.vision_config_path or None)
        detector_config = self.v2_config["vision"]["detector"]
        depth_config = self.v2_config["vision"]["depth"]
        alert_config = self.v2_config["alert"]
        if "QIMING_DEPTH_ENABLED" not in os.environ:
            settings.depth_enabled = bool(depth_config["enabled"])
        if "DEPTH_UPDATE_INTERVAL_SECONDS" not in os.environ:
            settings.depth_update_interval_seconds = 1.0 / float(depth_config["update_fps"])
        if "DEPTH_MAX_AGE_MS" not in os.environ:
            settings.depth_max_age_ms = int(depth_config["max_age_ms"])
        if "DEPTH_INPUT_SIZE" not in os.environ:
            settings.depth_input_size = int(depth_config["input_size"])
        if "YOLO_IMAGE_SIZE" not in os.environ:
            settings.yolo_image_size = int(detector_config["imgsz"])
        self.detector_enabled = bool(detector_config["enabled"])
        self.tracking_enabled = bool(self.v2_config["vision"]["tracking"]["enabled"])
        self.alerts_enabled = bool(alert_config["enabled"])
        self.alert_tts_enabled = bool(alert_config["tts_enabled"])
        self.alert_vibration_enabled = bool(alert_config["vibration_enabled"])
        self.yolo_half = detector_config["precision"] == "fp16"
        spatial_zones = self.v2_config["spatial"]["zones"]
        if "LEFT_RATIO" not in os.environ:
            settings.left_ratio = float(spatial_zones["left"])
        if "RIGHT_RATIO" not in os.environ:
            settings.right_ratio = float(spatial_zones["right"])
        self.devices = {}
        self.web_clients = set()
        self.latest = {}
        self.model = None
        self.model_error = None
        self.alert_last = {}
        self.speech_announced = {}
        self.motor_monitor_leases = {}
        self.locations = {}
        self.last_ocr = {}
        self.image_queue = asyncio.Queue(maxsize=1)
        self.inference_lock = asyncio.Lock()
        self.frame_timestamps = {}
        self.frame_wall_timestamps = {}
        self.depth_queue = asyncio.Queue(maxsize=1)
        self.speech_enabled = True
        self.tracker = TrackerAdapter()
        self.approach_detector = ApproachDetector()
        self.alert_controller = AlertController()
        self.alert_frame_index = defaultdict(int)
        self.depth_estimator = DepthAnythingEstimator(
            settings.depth_code_path, settings.depth_model_path,
            input_size=settings.depth_input_size,
        )
        self.depth_fusion = SpatialFusion()
        risk_config = self.v2_config["risk"]
        weights = tuple(float(risk_config[key]) for key in (
            "distance_weight", "path_weight", "motion_weight", "ttc_weight"))
        self.risk_engine = SpatialRiskEngine(max_depth_age_ms=settings.depth_max_age_ms, weights=weights)
        self.depth_results = {}
        self.depth_tracks = {}
        self.detection_history = {}
        self.depth_task = None
        self.depth_error = None
        self.depth_retry_at = 0.0
        self.metrics = {"yolo_frames": 0, "yolo_seconds": 0.0, "depth_frames": 0,
                        "depth_seconds": 0.0, "risk_seconds": 0.0}
        self.gpu_name = None
        self.gpu_total_memory_mb = None
        self.gpu_available = False
        self.yolo_wall_started = None
        self.depth_wall_started = None
        self.yolo_completion_times = deque(maxlen=60)
        self.depth_completion_times = deque(maxlen=60)
        self.alert_scheduler = AlertScheduler(handler=self._dispatch_alert_event)

    def enqueue_frame(self, source_id, seq, raw):
        frame = camera_frames.get(source_id)
        if frame is None or frame.seq != seq:
            return False
        captured_at = frame.received_at
        if self.image_queue.full():
            try:
                dropped = self.image_queue.get_nowait()
                self.frame_timestamps.pop((dropped[0], dropped[1]), None)
                self.frame_wall_timestamps.pop((dropped[0], dropped[1]), None)
                self.image_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            self.image_queue.put_nowait((source_id, seq, raw))
        except asyncio.QueueFull:
            return False
        self.frame_timestamps[(source_id, seq)] = captured_at
        self.frame_wall_timestamps[(source_id, seq)] = time.time()
        now = camera_frames._clock()
        for key, value in list(self.frame_timestamps.items()):
            if now - value > 30:
                self.frame_timestamps.pop(key, None)
        while len(self.frame_timestamps) > 64:
            key = next(iter(self.frame_timestamps))
            self.frame_timestamps.pop(key, None)
            self.frame_wall_timestamps.pop(key, None)
        return True

    def performance_snapshot(self, frame_latency_ms=None):
        yolo_frames = self.metrics["yolo_frames"]
        depth_frames = self.metrics["depth_frames"]
        def rolling_fps(samples):
            if len(samples) < 2 or samples[-1] <= samples[0]:
                return 0.0
            return round((len(samples) - 1) / (samples[-1] - samples[0]), 2)
        used_mb = None
        if self.gpu_available:
            try:
                import torch
                used_mb = round(torch.cuda.memory_allocated() / (1024 * 1024))
            except Exception:
                pass
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=None)
        except Exception:
            cpu_percent = None
        return {
            "yolo_fps": rolling_fps(self.yolo_completion_times),
            "depth_fps": rolling_fps(self.depth_completion_times),
            "risk_ms": round(self.metrics["risk_seconds"] / max(yolo_frames, 1) * 1000, 3),
            "gpu_name": self.gpu_name, "gpu_memory_used_mb": used_mb,
            "gpu_memory_total_mb": self.gpu_total_memory_mb,
            "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
            "frame_latency_ms": round(frame_latency_ms, 1) if frame_latency_ms is not None else None,
            "pending_yolo_frames": self.image_queue.qsize(),
            "pending_depth_frames": self.depth_queue.qsize(),
            "yolo_frames": yolo_frames, "depth_frames": depth_frames,
        }

    async def _dispatch_alert_event(self, event: AlertEvent):
        event_id = f"{event.source_id}:{event.track_key or event.track_id or 'unknown'}:{event.created_at:.6f}"
        # Risk alerts are the depth-aware speech channel.  L1 is a soft
        # warning, L2 is an immediate proximity warning, and L3 is urgent.
        # Keeping this separate from ordinary object announcements prevents a
        # high-risk target from being reduced to a generic “前方有物品” phrase.
        requested_voice = event.level in ("L1", "L2", "L3") and self.speech_enabled and self.alert_tts_enabled
        has_device = event.source_id in self.devices
        delivered = await self.send_device(event.source_id, {
            "type": "obstacle_alert", "device_id": event.source_id,
            "direction": event.direction, "object": event.object_class,
            "confidence": event.confidence,
            "vibration": event.vibration if self.alert_vibration_enabled else "stop",
            # Text is intentionally not sent through the camera WebSocket. The
            # independent TTS downlink handles audio after local phrase lookup.
            "voice_text": None,
            "alert_state": event.level, "track_id": event.track_id,
            "approach_reason": ";".join(event.reasons),
            "risk_score": event.score, "event_id": event_id,
            "frame_id": event.frame_id, "depth_frame_id": event.depth_frame_id,
            "relative_depth": event.relative_depth,
            "speech_priority": event.speech_priority,
        })
        tts_priority = {"L3": 0, "L2": 1, "L1": 2}.get(event.level, 2)
        speech_delivery = (
            tts_delivery.queue_text(event.source_id, event.message, priority=tts_priority)
            if requested_voice and has_device and event.message else
            {"status": "not_requested"}
        )
        voice_status = {
            "queued": "requested_on_device",
            "synthesizing": "synthesizing",
            "synthesis_unavailable": "synthesis_unavailable",
            "synthesis_queue_full": "tts_queue_full",
            "output_unconfigured": "output_unconfigured",
            "device_offline": "tts_channel_offline",
            "asset_missing": "phrase_asset_missing",
            "library_error": "tts_library_error",
            "queue_full": "tts_queue_full",
        }.get(speech_delivery.get("status"), "not_requested")
        alert = {
            "type": "obstacle_alert", "device_id": event.source_id,
            "direction": event.direction, "object": event.object_class,
            "confidence": event.confidence,
            "vibration": event.vibration if self.alert_vibration_enabled else "stop",
            "voice_text": event.message if requested_voice else None,
            "speech_enabled": requested_voice,
            "event_id": event_id,
            "delivery_status": "adapter_received" if delivered else "device_offline",
            "voice_status": voice_status if requested_voice else "not_requested",
            "alert_state": event.level, "track_id": event.track_id,
            "approach_reason": ";".join(event.reasons),
            "risk_score": event.score, "timestamp": now_iso(),
            "frame_id": event.frame_id, "depth_frame_id": event.depth_frame_id,
            "relative_depth": event.relative_depth,
            "speech_priority": event.speech_priority,
        }
        await self.broadcast(alert)
        if event.level != "L0":
            with db() as c:
                c.execute(
                    "INSERT INTO obstacle_logs(device_id,detected_at,object_class,direction,confidence,voice_text,created_at,risk_level,risk_score,risk_reasons,delivery_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (event.source_id, now_iso(), event.object_class, event.direction,
                     event.confidence, event.message, now_iso(), event.level,
                     event.score, ";".join(event.reasons), alert["delivery_status"]),
                )

    def motor_monitor_enabled(self, device_id):
        now = time.monotonic()
        for username, (leased_device, expires_at) in list(self.motor_monitor_leases.items()):
            if expires_at <= now:
                self.motor_monitor_leases.pop(username, None)
        return any(device == device_id for device, _ in self.motor_monitor_leases.values())

    async def set_motor_monitor(self, username, device_id=None, active=False):
        previous = self.motor_monitor_leases.get(username)
        if active:
            self.motor_monitor_leases[username] = (device_id, time.monotonic() + 15)
            return
        self.motor_monitor_leases.pop(username, None)
        if previous and previous[0] and not self.motor_monitor_enabled(previous[0]):
            await self.send_device(previous[0], {
                "type": "device_command", "command": "stop", "device_id": previous[0]
            })

    def announce_objects(self, device_id, objects, now=None):
        if not self.speech_enabled:
            return False
        device = self.devices.get(device_id)
        speak_text = getattr(device, "speak_text", None)
        if not callable(speak_text):
            return False

        now = time.monotonic() if now is None else now
        states = self.speech_announced.setdefault(device_id, {})
        observations = {}
        for item in objects:
            if self.alerts_enabled and str((item.get("risk") or {}).get("level", "")) in ("L1", "L2", "L3"):
                continue
            object_class = str(item.get("class", "")).strip().lower()
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if not object_class or confidence < settings.yolo_confidence:
                continue
            direction = item.get("direction")
            if direction not in ("left", "front", "right"):
                direction = "front"
            track_id = item.get("track_id")
            target_key = (object_class, direction, str(track_id) if track_id is not None else None)
            observations[target_key] = (object_class, direction)

        for target_key, state in list(states.items()):
            if target_key not in observations and now - state["last_seen"] > OBJECT_SPEECH_LOST_SECONDS:
                states.pop(target_key, None)

        candidates = {}
        for target_key, (object_class, direction) in observations.items():
            state = states.get(target_key)
            if state is None or now - state["last_seen"] > OBJECT_SPEECH_LOST_SECONDS:
                state = {"frames": 0, "last_seen": now, "spoken": False}
                states[target_key] = state
            state["frames"] += 1
            state["last_seen"] = now
            if state["frames"] < OBJECT_SPEECH_CONFIRM_FRAMES or state["spoken"]:
                continue
            name = OBJECT_NAMES_ZH.get(object_class, object_class)
            candidates.setdefault((direction, name), []).append(state)

        selected = list(candidates.items())[:MAX_OBJECTS_PER_ANNOUNCEMENT]
        if not selected:
            return False

        direction_names = {"front": "前方", "left": "左侧", "right": "右侧"}
        groups = {"front": [], "left": [], "right": []}
        for (direction, name), _states in selected:
            groups[direction].append(name)
        text = "，".join(
            direction_names[direction] + "有" + "、".join(names)
            for direction, names in groups.items() if names
        ) + "。"

        if not speak_text(text):
            return False
        for _key, target_states in selected:
            for state in target_states:
                state["spoken"] = True
        return True

    async def load_model(self):
        if not self.detector_enabled:
            self.model_error = "YOLO 已在 config/v2.yaml 中关闭"
            return
        try:
            from ultralytics import YOLO
            import os
            if not os.path.exists(settings.yolo_weights):
                self.model_error = f"YOLO 权重不存在: {settings.yolo_weights}"
                return
            self.model = YOLO(settings.yolo_weights)
            try:
                import torch
                self.gpu_available = bool(torch.cuda.is_available())
                if self.gpu_available:
                    props = torch.cuda.get_device_properties(0)
                    self.gpu_name = props.name
                    self.gpu_total_memory_mb = round(props.total_memory / (1024 * 1024))
            except Exception:
                self.gpu_available = False
        except Exception as e:
            self.model_error = str(e)
            log_system("ERROR", "yolo_init", str(e))

    async def load_depth_model(self):
        if not settings.depth_enabled:
            self.depth_error = "disabled_by_config"
            return
        try:
            await asyncio.to_thread(
                ensure_depth_assets, settings.depth_code_path, settings.depth_model_path,
            )
            await asyncio.to_thread(self.depth_estimator.load)
            self.depth_error = None
        except Exception as error:
            self.depth_error = str(error)
            self.depth_retry_at = time.monotonic() + 60.0
            log_system("WARNING", "depth_init", self.depth_error)

    async def depth_loop(self):
        while True:
            source_id, seq, raw, captured_at = await self.depth_queue.get()
            try:
                now = time.monotonic()
                if not settings.depth_enabled:
                    continue
                if self.depth_error:
                    if time.monotonic() < self.depth_retry_at:
                        continue
                    self.depth_error = None
                    if self.depth_estimator.model is None:
                        await asyncio.to_thread(ensure_depth_assets, settings.depth_code_path, settings.depth_model_path)
                        await asyncio.to_thread(self.depth_estimator.load)
                image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                started = time.perf_counter()
                depth = await asyncio.to_thread(self.depth_estimator.predict, image)
                elapsed = time.perf_counter() - started
                self.depth_results[source_id] = {
                    "frame_id": seq, "captured_at": captured_at or time.monotonic(),
                    "processed_at": time.monotonic(),
                    "depth": depth,
                    "frame_width": int(image.shape[1]), "frame_height": int(image.shape[0]),
                }
                frame_result = self.detection_history.get((source_id, seq))
                if frame_result and frame_result.get("model_status") == "ready":
                    fused = self.depth_fusion.fuse(seq, frame_result["objects"], depth)
                    by_track = {}
                    for obj in fused:
                        track_id = obj.get("track_id")
                        try:
                            track_id = int(track_id)
                        except (TypeError, ValueError):
                            continue
                        measurement = {
                            "frame_id": seq, "captured_at": captured_at or time.monotonic(),
                            **obj["depth"],
                        }
                        by_track[int(track_id)] = measurement
                        self.depth_tracks[(source_id, int(track_id))] = measurement
                        obj["risk"] = self.risk_engine.evaluate(
                            source_id, obj, now=time.time(), measurement_age_ms=0,
                            camera_motion_quality=1.0 if frame_result.get("tracker") in ("botsort", "bytetrack") else 0.0,
                        )
                        evaluation = obj["risk"]
                        if (self.alerts_enabled and evaluation["status"] == "valid"
                                and evaluation["level"] in ("L1", "L2", "L3")):
                            object_class = str(obj.get("class", "unknown"))
                            name = OBJECT_NAMES_ZH.get(object_class, object_class)
                            direction = str(obj.get("direction", "front"))
                            side = {"left": "左侧", "right": "右侧"}.get(direction, "前方")
                            depth_label = {"near": "较近", "mid": "中等距离", "far": "较远"}.get(
                                str((obj.get("depth") or {}).get("level", "")), ""
                            )
                            level = evaluation["level"]
                            if level == "L1":
                                message = f"注意，{side}有{name}" + (f"，相对距离{depth_label}" if depth_label else "")
                            elif level == "L2":
                                message = f"注意，{side}有{name}，距离较近"
                            else:
                                message = f"危险，{side}有{name}，请注意"
                            await self.alert_scheduler.submit(AlertEvent(
                            source_id=source_id, track_id=track_id, track_key=f"track:{track_id}",
                                level=level, direction=direction, object_class=object_class,
                                message=message, score=evaluation["score"],
                                reasons=tuple(evaluation["reasons"]), created_at=time.monotonic(),
                                confidence=float(obj.get("confidence", 0.0)), vibration=("both" if level == "L3" else direction),
                                frame_id=seq, depth_frame_id=seq,
                                relative_depth=obj.get("depth"),
                                speech_priority={"L1": 1, "L2": 2, "L3": 3}[level],
                            ))
                    frame_result["depth_status"] = "ready"
                    frame_result["depth_frame_id"] = seq
                    frame_result["objects"] = fused
                    image8 = np.clip((depth - np.nanpercentile(depth, 2)) /
                                     max(float(np.nanpercentile(depth, 98) - np.nanpercentile(depth, 2)), 1e-6)
                                     * 255, 0, 255).astype(np.uint8)
                    preview = cv2.applyColorMap(image8, cv2.COLORMAP_TURBO)
                    ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 72])
                    update = {
                        "type": "spatial_update", "device_id": source_id,
                        "image_seq": seq, "capture_timestamp": frame_result.get("capture_timestamp"),
                        "depth_status": "ready", "depth_frame_id": seq,
                        "depth_preview": base64.b64encode(encoded).decode("ascii") if ok else None,
                        "raw_frame_preview": base64.b64encode(raw).decode("ascii"),
                        "frame_width": int(image.shape[1]), "frame_height": int(image.shape[0]),
                        "depth_device": self.depth_estimator.device,
                        "depth_precision": self.depth_estimator.precision,
                        "performance": self.performance_snapshot((time.monotonic() - (captured_at or time.monotonic())) * 1000),
                        "objects": frame_result["objects"],
                    }
                    await self.broadcast(update)
                    self.latest[source_id] = {"at": time.time(), "result": frame_result}
                self.metrics["depth_frames"] += 1
                self.metrics["depth_seconds"] += elapsed
                self.depth_completion_times.append(time.monotonic())
                if self.depth_wall_started is None:
                    self.depth_wall_started = time.monotonic()
            except DepthModelError as error:
                self.depth_error = str(error)
                self.depth_retry_at = time.monotonic() + (10.0 if "显存不足" in self.depth_error else 60.0)
            except Exception as error:
                self.depth_error = f"深度线程异常：{error}"
                self.depth_retry_at = time.monotonic() + 30.0
            finally:
                self.depth_queue.task_done()

    def enqueue_depth(self, source_id, seq, raw, captured_at):
        if not settings.depth_enabled or self.depth_error:
            if not settings.depth_enabled or time.monotonic() < self.depth_retry_at:
                return
            self.depth_error = None
        current = self.depth_results.get(source_id)
        if current and time.time() - current["processed_at"] < settings.depth_update_interval_seconds:
            return
        if self.depth_queue.full():
            try:
                self.depth_queue.get_nowait()
                self.depth_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            self.depth_queue.put_nowait((source_id, seq, raw, captured_at))
        except asyncio.QueueFull:
            pass

    def depth_for_track(self, source_id, track_id):
        if track_id is None:
            return None
        try:
            item = self.depth_tracks.get((source_id, int(track_id)))
        except (TypeError, ValueError):
            return None
        if not item:
            return None
        age_ms = (time.monotonic() - item["captured_at"]) * 1000
        if age_ms < 0 or age_ms > settings.depth_max_age_ms:
            return None
        return item, max(0.0, age_ms)

    def direction(self, cx, width):
        ratio = cx / max(width, 1)
        return "left" if ratio < settings.left_ratio else "right" if ratio > settings.right_ratio else "front"

    async def detect_loop(self):
        while True:
            device_id, seq, raw = await self.image_queue.get()
            captured_at = self.frame_timestamps.pop((device_id, seq), None)
            captured_wall = self.frame_wall_timestamps.pop((device_id, seq), None)
            yolo_started = time.perf_counter()
            if seq <= 1:
                self.tracker.reset(self.model, device_id)
                self.approach_detector.reset(device_id)
                self.alert_controller.reset(device_id)
                self.risk_engine.reset(device_id)
                self.alert_scheduler.clear(device_id)
                self.alert_frame_index[device_id] = 0
            self.alert_frame_index[device_id] += 1
            frame_index = self.alert_frame_index[device_id]
            result = {"type":"detection_update","device_id":device_id,"image_seq":seq,"objects":[],"model_status":"disabled" if not self.detector_enabled else "unavailable" if self.model_error or self.model is None else "ready","timestamp":now_iso(),"risk_alerts_enabled":self.alerts_enabled}
            if captured_at is None:
                frame = camera_frames.get(device_id)
                captured_at = frame.received_at if frame and frame.seq == seq else time.monotonic()
            result["capture_timestamp"] = datetime.fromtimestamp(
                captured_wall or time.time(), timezone.utc,
            ).isoformat()
            result["processing_timestamp"] = time.time()
            result["depth_status"] = "disabled" if not settings.depth_enabled else ("unavailable" if self.depth_error else "waiting")
            tracker_name = "simulation" if settings.simulation_mode and device_id.startswith("simulator_") else None
            risk_speech_present = False
            if settings.simulation_mode and device_id.startswith("simulator_"):
                result["model_status"] = "simulation"
                result["objects"] = [{"class":"person","confidence":0.96,"bbox":[80,60,260,360],"direction":"left","track_id":1}]
                result["simulation"] = True
            if self.model and not (settings.simulation_mode and device_id.startswith("simulator_")):
                try:
                    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                    if img is None: raise ValueError("invalid jpeg")
                    result["frame_width"] = int(img.shape[1])
                    result["frame_height"] = int(img.shape[0])
                    async with self.inference_lock:
                        pred, tracker_name = await asyncio.to_thread(
                            self.tracker.infer,
                            self.model,
                            img,
                            source_id=device_id,
                            conf=settings.yolo_confidence,
                            imgsz=settings.yolo_image_size,
                            half=self.yolo_half and self.gpu_available,
                            tracking_enabled=self.tracking_enabled,
                            device="cuda:0" if self.gpu_available else "cpu",
                        )
                    names = self.model.names
                    # Ultralytics' Results iterator can raise IndexError for
                    # an empty/malformed camera result.  Treat that one frame
                    # as an empty detection instead of poisoning the stream.
                    try:
                        predictions = [] if pred is None else list(pred)
                    except (IndexError, TypeError, ValueError, AttributeError) as error:
                        predictions = []
                        result["detection_warning"] = f"结果帧已跳过：{type(error).__name__}"
                    boxes = getattr(predictions[0], "boxes", None) if predictions else None
                    for box in boxes or []:
                        try:
                            x1,y1,x2,y2 = [float(x) for x in box.xyxy[0].tolist()]
                            conf = float(box.conf[0]); cls = int(box.cls[0])
                            if isinstance(names, dict):
                                class_name = names.get(cls, str(cls))
                            elif 0 <= cls < len(names):
                                class_name = names[cls]
                            else:
                                class_name = str(cls)
                            track_id = None
                            if getattr(box, "id", None) is not None:
                                try:
                                    track_id = int(box.id[0])
                                except (TypeError, ValueError, IndexError):
                                    track_id = None
                            item = {"class": class_name, "confidence": round(conf,3), "bbox":[x1,y1,x2,y2], "direction":self.direction((x1+x2)/2, img.shape[1])}
                            if track_id is not None:
                                item["track_id"] = track_id
                            result["objects"].append(item)
                        except (IndexError, KeyError, TypeError, ValueError, AttributeError):
                            # A malformed box must not turn an otherwise valid frame into a service error.
                            continue
                    result["model_status"] = "ready"
                except IndexError as e:
                    # Ultralytics/tracker occasionally indexes an empty
                    # Results list for a camera frame. The model is still
                    # loaded, so skip this frame and keep the live stream.
                    result["model_status"] = "ready"
                    result["objects"] = []
                    result["detection_warning"] = f"检测帧已跳过：{type(e).__name__}"
                    log_system("WARNING", "yolo_frame_skipped", str(e))
                except Exception as e:
                    result["model_status"] = "error"; result["error"] = str(e)
            if tracker_name:
                result["tracker"] = tracker_name
            if result["model_status"] in ("ready", "simulation"):
                frame_width = result.get("frame_width", 320)
                frame_height = result.get("frame_height", 240)
                observations = self.approach_detector.update(
                    device_id, result["objects"], frame_width, frame_height,
                )
                for observation in observations:
                    if 0 <= observation.object_index < len(result["objects"]):
                        result["objects"][observation.object_index]["approach"] = observation.as_dict()
                depth_ages = []
                for obj in result["objects"]:
                    measurement = self.depth_for_track(device_id, obj.get("track_id"))
                    if measurement:
                        depth_meta, depth_age_ms = measurement
                        obj["depth"] = {key: depth_meta.get(key) for key in (
                            "frame_id", "depth_median", "depth_p10", "depth_p90",
                            "depth_percentile", "valid_fraction", "quality", "level", "unit",
                        )}
                        obj["depth"]["measurement_age_ms"] = round(depth_age_ms)
                        depth_ages.append(depth_age_ms)
                if depth_ages:
                    result["depth_status"] = "ready"
                    result["depth_frame_id"] = max(obj["depth"]["frame_id"] for obj in result["objects"] if obj.get("depth"))
                    result["depth_age_ms"] = round(min(depth_ages))
                elif settings.depth_enabled and not self.depth_error:
                    result["depth_status"] = "stale"
                risk_started = time.perf_counter()
                for object_index, obj in enumerate(result["objects"]):
                    evaluation = self.risk_engine.evaluate(
                        device_id, obj, now=time.time(),
                        measurement_age_ms=(obj.get("depth") or {}).get("measurement_age_ms", settings.depth_max_age_ms + 1),
                        camera_motion_quality=1.0 if result.get("tracker") in ("botsort", "bytetrack") else 0.0,
                    )
                    obj["risk"] = evaluation
                    if (self.alerts_enabled and evaluation["status"] == "valid"
                            and evaluation["level"] in ("L1", "L2", "L3")):
                        risk_speech_present = True
                        try:
                            track_id = int(obj["track_id"])
                        except (KeyError, TypeError, ValueError):
                            track_id = None
                        if track_id is None:
                            continue
                        level = evaluation["level"]
                        direction = str(obj.get("direction", "front"))
                        object_class = str(obj.get("class", "unknown"))
                        name = OBJECT_NAMES_ZH.get(object_class, object_class)
                        side = {"left": "左侧", "right": "右侧"}.get(direction, "前方")
                        depth_label = {"near": "较近", "mid": "中等距离", "far": "较远"}.get(
                            str((obj.get("depth") or {}).get("level", "")), ""
                        )
                        if level == "L1":
                            message = f"注意，{side}有{name}" + (f"，相对距离{depth_label}" if depth_label else "")
                        elif level == "L2":
                            message = f"注意，{side}有{name}，距离较近"
                        else:
                            message = f"危险，{side}有{name}，请注意"
                        await self.alert_scheduler.submit(AlertEvent(
                            source_id=device_id, track_id=track_id,
                            track_key=f"track:{track_id}", level=level,
                            direction=direction, object_class=object_class,
                            message=message, score=evaluation["score"],
                            reasons=tuple(evaluation["reasons"]), created_at=time.monotonic(),
                            confidence=float(obj.get("confidence", 0.0)),
                            vibration=("both" if level == "L3" else direction) if level != "L0" else "stop",
                            frame_id=seq,
                            depth_frame_id=(obj.get("depth") or {}).get("frame_id"),
                            relative_depth=obj.get("depth"),
                            speech_priority={"L1": 1, "L2": 2, "L3": 3}[level],
                        ))
                    observation_index = next((index for index, item in enumerate(observations)
                                              if item.object_index == object_index), -1)
                    if (evaluation["status"] == "valid" and evaluation["level"] == "L3"
                            and evaluation.get("visual_expansion_per_second") is not None
                            and 0 <= observation_index < len(observations)):
                        old = observations[observation_index]
                        observations[observation_index] = replace(old, urgent_candidate=True)
                        obj["approach"] = observations[observation_index].as_dict()
                self.metrics["risk_seconds"] += time.perf_counter() - risk_started
                decisions = self.alert_controller.update(
                    device_id, observations, time.time(), frame_index,
                )
            else:
                # Advance the state machine on failed/stale detections so an
                # active alert can settle and issue a single STOP event.
                self.approach_detector.update(device_id, [], 320, 240)
                decisions = self.alert_controller.update(
                    device_id, [], time.time(), frame_index,
                )
            self.latest[device_id] = {"at":time.time(),"result":result}
            self.detection_history[(device_id, seq)] = result
            for key in list(self.detection_history):
                if key[0] == device_id and key[1] < seq - 16:
                    self.detection_history.pop(key, None)
            if result["model_status"] in ("ready", "simulation") and not (settings.simulation_mode and device_id.startswith("simulator_")):
                self.enqueue_depth(device_id, seq, raw, captured_at)
            if result["model_status"] == "ready":
                self.metrics["yolo_frames"] += 1
                self.metrics["yolo_seconds"] += time.perf_counter() - yolo_started
                self.yolo_completion_times.append(time.monotonic())
                if self.yolo_wall_started is None:
                    self.yolo_wall_started = time.monotonic()
            result["performance"] = self.performance_snapshot(
                (time.monotonic() - captured_at) * 1000,
            )
            await self.broadcast(result)
            urgent_speech_sent = False
            for decision in decisions:
                object_name = OBJECT_NAMES_ZH.get(decision.object_class, decision.object_class)
                side = {"left": "左侧", "right": "右侧"}.get(decision.direction, "前方")
                text = f"{side}有{object_name}"
                level = {AlertLevel.NORMAL: "L0", AlertLevel.APPROACH: "L1", AlertLevel.URGENT: "L3"}[decision.level]
                matching = next((obj for obj in result["objects"]
                                 if obj.get("track_id") == decision.track_id
                                 and obj.get("class") == decision.object_class), {})
                risk = matching.get("risk") or {}
                relative_depth = matching.get("depth")
                confidence = float(matching.get("confidence", 0.0))
                if level == "L1" and relative_depth:
                    depth_label = {"near": "较近", "mid": "中等距离", "far": "较远"}.get(
                        str(relative_depth.get("level", "")), ""
                    )
                    if depth_label:
                        text += f"，相对距离{depth_label}"
                elif level == "L2":
                    text += "，距离较近"
                elif level == "L3":
                    text += "，请注意"
                if level == "L3":
                    urgent_speech_sent = True
                event = AlertEvent(
                    source_id=device_id, track_id=decision.track_id,
                    level=level, direction=decision.direction,
                    object_class=decision.object_class, message=text,
                    score=risk.get("score"), reasons=tuple(risk.get("reasons", [decision.reason])),
                    created_at=time.monotonic(), confidence=confidence,
                    track_key=decision.track_key, vibration=decision.vibration,
                    frame_id=seq,
                    depth_frame_id=(relative_depth or {}).get("frame_id"),
                    relative_depth=relative_depth,
                    speech_priority={"L1": 1, "L2": 2, "L3": 3}.get(level, 0),
                )
                if self.alerts_enabled:
                    await self.alert_scheduler.submit(event)
            if result["model_status"] == "ready" and self.alerts_enabled and not urgent_speech_sent and not risk_speech_present:
                self.announce_objects(device_id, result["objects"])
            self.image_queue.task_done()
            for key, depth_track in list(self.depth_tracks.items()):
                if key[0] == device_id and time.monotonic() - depth_track["captured_at"] > settings.depth_max_age_ms / 1000:
                    self.depth_tracks.pop(key, None)

    async def broadcast(self, msg):
        dead=[]
        for ws in self.web_clients:
            try: await ws.send_json(msg)
            except Exception: dead.append(ws)
        for ws in dead: self.web_clients.discard(ws)
        # Deliver device-scoped events only to that device's authorized
        # remote-guard sockets; private location data is never broadcast.
        device_id = msg.get("device_id") if isinstance(msg, dict) else None
        if device_id and not str(msg.get("type", "")).startswith("family_"):
            from .remote_guard import publish_guard_event
            await publish_guard_event(str(device_id), msg)

    async def send_device(self, device_id, msg):
        ws = self.devices.get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(msg)
            return True
        except Exception:
            if self.devices.get(device_id) is ws:
                self.devices.pop(device_id,None)
            return False

runtime = Runtime()

async def environment_reply(device_id, text):
    item=runtime.latest.get(device_id)
    # 软件联调时，当前网页摄像头是用户真正要查询的画面；选择更新时间更近的缓存。
    local_item=runtime.latest.get("local_camera")
    if local_item and (not item or local_item["at"] >= item["at"]):
        item=local_item
    if not item or time.time()-item["at"] > settings.cache_ttl:
        return "当前无法获取可靠环境信息。", "environment_query"
    objs=item["result"]["objects"]
    wanted = "left" if "左" in text else "right" if "右" in text else "front" if "前" in text else None
    objs=[o for o in objs if wanted is None or o["direction"]==wanted]
    if not objs: return "当前画面未检测到相关障碍物。", "environment_query"
    return "当前摄像头画面中，"+"，".join(("左侧" if o["direction"]=="left" else "右侧" if o["direction"]=="right" else "前方")+"有"+o["class"] for o in objs)+"。", "environment_query"

async def dialogue(device_id, text):
    if any(k in text for k in ("前面","前方","左边","左侧","右边","右侧","周围","障碍","当前画面","摄像头","看到什么","看到了什么","描述画面")):
        return await environment_reply(device_id,text)
    if settings.openai_base_url and settings.openai_api_key and settings.openai_model:
        try:
            import httpx
            headers={"Authorization":f"Bearer {settings.openai_api_key}"}
            payload={"model":settings.openai_model,"messages":[{"role":"user","content":text}],"temperature":0.3}
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                r=await client.post(settings.openai_base_url.rstrip("/")+"/chat/completions",headers=headers,json=payload); r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"], "dialogue"
        except Exception as e: log_system("ERROR","dialogue",str(e))
    return "当前未配置大模型服务，我可以继续为你提供环境检测。", "dialogue"
