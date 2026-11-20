import asyncio, base64, json, time
from collections import defaultdict
import cv2, numpy as np
from .config import settings
from .database import db, now_iso, log_system
from .vision import AlertController, AlertLevel, ApproachDetector, TrackerAdapter

OBJECT_NAMES_ZH = {
    "person": "行人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
    "bus": "公交车", "train": "火车", "truck": "卡车", "boat": "船",
    "traffic light": "红绿灯", "stop sign": "停车标志", "bench": "长椅",
    "bird": "鸟", "cat": "猫", "dog": "狗", "horse": "马", "sheep": "羊",
    "cow": "牛", "backpack": "背包", "umbrella": "雨伞", "handbag": "手提包",
    "suitcase": "行李箱", "sports ball": "球", "bottle": "瓶子", "cup": "水杯",
    "chair": "椅子", "couch": "沙发", "potted plant": "盆栽", "bed": "床",
    "dining table": "餐桌", "toilet": "马桶", "laptop": "笔记本电脑",
    "cell phone": "手机", "microwave": "微波炉", "oven": "烤箱", "sink": "水槽",
    "refrigerator": "冰箱", "book": "书", "clock": "时钟", "vase": "花瓶",
}

class Runtime:
    def __init__(self):
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
        self.speech_enabled = True
        self.tracker = TrackerAdapter()
        self.approach_detector = ApproachDetector()
        self.alert_controller = AlertController()
        self.alert_frame_index = defaultdict(int)

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

    async def load_model(self):
        try:
            from ultralytics import YOLO
            import os
            if not os.path.exists(settings.yolo_weights):
                self.model_error = f"YOLO 权重不存在: {settings.yolo_weights}"
                return
            self.model = YOLO(settings.yolo_weights)
        except Exception as e:
            self.model_error = str(e)
            log_system("ERROR", "yolo_init", str(e))

    def direction(self, cx, width):
        ratio = cx / max(width, 1)
        return "left" if ratio < settings.left_ratio else "right" if ratio > settings.right_ratio else "front"

    async def detect_loop(self):
        while True:
            device_id, seq, raw = await self.image_queue.get()
            if seq <= 1:
                self.tracker.reset(self.model, device_id)
                self.approach_detector.reset(device_id)
                self.alert_controller.reset(device_id)
                self.alert_frame_index[device_id] = 0
            self.alert_frame_index[device_id] += 1
            frame_index = self.alert_frame_index[device_id]
            result = {"type":"detection_update","device_id":device_id,"image_seq":seq,"objects":[],"model_status":"unavailable" if self.model_error or self.model is None else "ready","timestamp":now_iso()}
            tracker_name = "simulation" if settings.simulation_mode and device_id.startswith("simulator_") else None
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
                    pred, tracker_name = await asyncio.to_thread(
                        self.tracker.infer,
                        self.model,
                        img,
                        source_id=device_id,
                        conf=settings.yolo_confidence,
                        imgsz=768,
                    )
                    names = self.model.names
                    for box in pred[0].boxes:
                        x1,y1,x2,y2 = [float(x) for x in box.xyxy[0].tolist()]
                        conf = float(box.conf[0]); cls = int(box.cls[0])
                        track_id = None
                        if getattr(box, "id", None) is not None:
                            try:
                                track_id = int(box.id[0])
                            except (TypeError, ValueError, IndexError):
                                track_id = None
                        item = {"class": names[cls], "confidence": round(conf,3), "bbox":[x1,y1,x2,y2], "direction":self.direction((x1+x2)/2, img.shape[1])}
                        if track_id is not None:
                            item["track_id"] = track_id
                        result["objects"].append(item)
                    result["model_status"] = "ready"
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
            await self.broadcast(result)
            for decision in decisions:
                object_name = OBJECT_NAMES_ZH.get(decision.object_class, decision.object_class)
                side = {"left": "左侧", "right": "右侧"}.get(decision.direction, "前方")
                text = f"{side}有{object_name}"
                if decision.level == AlertLevel.URGENT:
                    text += "，请注意"
                voice_text = text if decision.level == AlertLevel.URGENT and self.speech_enabled else None
                alert = {
                    "type": "obstacle_alert",
                    "device_id": device_id,
                    "direction": decision.direction,
                    "object": decision.object_class,
                    "confidence": 0.0,
                    "vibration": decision.vibration,
                    "voice_text": voice_text,
                    "alert_state": decision.level.value,
                    "track_id": decision.track_id,
                    "approach_reason": decision.reason,
                    "timestamp": now_iso(),
                }
                for obj in result["objects"]:
                    if obj.get("track_id") == decision.track_id and obj.get("class") == decision.object_class:
                        alert["confidence"] = obj.get("confidence", 0.0)
                        break
                await self.send_device(device_id, alert)
                await self.broadcast(alert)
                if decision.level != AlertLevel.NORMAL:
                    with db() as c:
                        c.execute(
                            "INSERT INTO obstacle_logs(device_id,detected_at,object_class,direction,confidence,voice_text,created_at) VALUES(?,?,?,?,?,?,?)",
                            (device_id, now_iso(), decision.object_class, decision.direction,
                             alert["confidence"], text, now_iso()),
                        )

    async def broadcast(self, msg):
        dead=[]
        for ws in self.web_clients:
            try: await ws.send_json(msg)
            except Exception: dead.append(ws)
        for ws in dead: self.web_clients.discard(ws)

    async def send_device(self, device_id, msg):
        ws = self.devices.get(device_id)
        if ws:
            try: await ws.send_json(msg)
            except Exception:
                if self.devices.get(device_id) is ws:
                    self.devices.pop(device_id,None)

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
