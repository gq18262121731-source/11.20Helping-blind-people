# V2.0 API

- `GET /api/health`：返回 YOLO、Depth 和设备状态。
- `GET /api/spatial/status`：返回深度模型、风险评分单位和运行性能统计。
- `/ws/web`：现有 WebSocket 新增 `spatial_update`，字段包含 `depth_status`、`depth_frame_id`、`depth_preview`、对象空间信息和风险结果。
- `obstacle_alert`：保留原有字段，并增加 `risk_score`、`approach_reason`、`event_id`、`delivery_status`、`voice_status`。

## 语音与识字工作台

所有接口要求登录 `Authorization: Bearer <token>`。环境查询会先运行现有 `IntentClassifier`，只对 `ENV_QUERY` / `SCENE_DESCRIBE` 请求执行视觉链路；同一任务中的 YOLO 和 VLM 使用同一张上传图片或同一设备帧。VLM 只生成场景描述，最终回复由意图、结构化检测结果和该描述共同组织。

- `GET /api/voice-vision/capabilities`：返回意图分类、YOLO、OCR、VLM、ASR、浏览器朗读，以及设备连接、本地语音库和 Edge TTS/FFmpeg 状态。
- `POST /api/voice-vision/tasks`：提交 JSON（`text`、`source_id`、`source_mode`、可选 `frame_seq`、可选显式 `intent`），或以 `image/jpeg` / `image/png` 请求体提交图片并通过查询参数提供文本与来源。响应包含意图、置信度、YOLO 快照、OCR/VLM 结果、最终回复、模型、错误码及各阶段耗时。
- `GET /api/voice-vision/tasks?source_mode=hardware|software|simulation&intent=ENV_QUERY&status=partial&limit=100`：按来源、意图和回复状态筛选历史任务。
- `PATCH /api/voice-vision/tasks/{task_id}/tts`：回写 `browser` / `simulation` 播报状态；帽端播放通过下一接口提交，不能由浏览器伪造完成回执。
- `POST /api/voice-vision/tasks/{task_id}/speak`：将已有任务/识字回复再次加入帽端 TTS 队列。缓存命中直接排队；未命中时返回 `synthesizing`，Edge TTS 合成并缓存后自动发送。帽端摄像头来源下的初次识字/查询由 `tts_target=device` 自动入队。
- `GET /api/tts/library`：读取缓存条数、设备连接与实时合成能力。
- `POST /api/tts/speak`：按 `device_id` 和 `text` 或 `phrase_id` 发起帽端播报，可设优先级 0–9；每设备合成/播报队列有上限。
- `POST /api/tts/stop`：取消设备的合成、当前播放和待播内容。
- `/ws/tts?device_id=<device_id>`：帽端独立 TTS 通道。READY 值 0 表示 I2S 输出可用，值 1 表示未配置；播放时回传开始、完成、失败或取消状态。任务回执会更新记录并推送给网页。

`POST /api/simulate/voice` 保留为软件联调兼容入口，并复用同一意图与任务处理器。`/api/voices` 仍用于旧交互记录；新页面以 `multimodal_tasks` 回看任务各阶段。
