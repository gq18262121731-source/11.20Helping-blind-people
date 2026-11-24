# WebSocket 通信协议（第一版）

## 当前灰度硬件入口

已新增 `/ws/camera` 直接接收一消息一帧、固定 76,800 字节的 320×240 灰度像素。
默认兼容原始 L/R 固件；`?device_id=esp32s3_cam_001&protocol=2` 增加文本 ACK 和
二进制 B（双侧）/ S（停止）。完整格式、启动命令和引脚冲突见
[ESP32-S3 灰度摄像头联调](esp32s3_gray_camera.md)。它不使用下文的 QH 头。

下文描述旧 `/ws/device` 协议及部分规划内容。完整音频、设备鉴权、分片重组、
错误码和执行回执尚未全部实现，不能把规划当作当前能力。

## 端点

- 设备：`ws://<电脑局域网IP>:8765/ws/device`
- 网页：`ws://<电脑局域网IP>:8765/ws/web`
- 音频：`ws://<电脑局域网IP>:8765/ws/audio`

端口由启动命令指定，路径在路由中固定。连接后先发送 `device_online`，随后每 10 秒发送 `heartbeat`。旧端点目前未强制校验设备共享密钥，仅用于可信局域网联调。

## 音频会话（软件阶段已实现）

音频通道与视频通道独立。先发送 `audio_start`，再发送二进制 PCM 分块，最后发送
`audio_end`；取消时发送 `audio_cancel`。PCM 固定为 16kHz、16bit、单声道、小端序。
二进制分块可直接发送 PCM，也可使用下方 QH 固定头（`frame_type=2`）：

```json
{"type":"audio_start","device_id":"morning_star_001","session_id":"session_001","sample_rate":16000,"channels":1,"bits_per_sample":16}
{"type":"audio_end","session_id":"session_001"}
```

服务端一次只接受每个设备一个会话，分块必须为偶数字节且序号递增；超过大小、格式错误、超时或断线会回到 `LISTENING`，不会阻塞视觉检测。软件模拟可运行
`python tools/audio_simulator.py sample.wav`，使用与 ESP32 相同的 WebSocket 入口。

固件侧已预留独立 `audioWs` 连接和 `beginAudioSession` / `sampleAudioFrame` /
`endAudioSession` 调用；后续硬件阶段只需把按键或 VAD 触发接到这三个调用，并校准
MAX9814 的 ADC 偏置，不需要改变服务端协议。

## JSON 公共字段

`type`（必填）、`device_id`、`seq`（单调递增序列号）、`timestamp`（UTC ISO 8601）、`request_id`（需要应答的消息）。未知类型返回 `error`，不应导致连接关闭。

## 控制与状态消息

```json
{"type":"device_online","device_id":"qiming_hat_001","firmware_version":"0.1.0","capabilities":{"camera":true,"audio_in":true,"speaker":true},"timestamp":"2026-01-01T00:00:00Z"}
{"type":"heartbeat","device_id":"qiming_hat_001","battery_percent":null,"camera_ok":true,"mic_ok":true,"timestamp":"2026-01-01T00:00:00Z"}
{"type":"device_command","command":"vibrate","direction":"left","duration_ms":300,"request_id":"abc"}
{"type":"device_command","command":"speak","text":"左侧有行人","audio_format":"pcm_s16le","sample_rate":16000,"request_id":"abc"}
```

## 二进制帧头

二进制 WebSocket 帧必须使用以下小端固定头，后接 payload；不得把任意二进制当 JSON 字符串：

`magic[2] = QH`、`version[1]`、`frame_type[1]`（1=image/jpeg，2=audio/pcm 预留）、`header_len[2] = 26`、`seq[4]`、`timestamp_ms[8]`、`chunk_index[2]`、`chunk_total[2]`、`payload_len[4]`、`payload[variable]`。设备 ID 由先前的 JSON 上线消息关联，不在这个固定头中。

当前旧端点只实现部分头部解析和整帧图像接收，尚未完整校验长度、序列号、设备身份或实现分片重组。

## 预警与检测消息

```json
{"type":"detection_update","device_id":"qiming_hat_001","image_seq":42,"objects":[{"class":"person","confidence":0.95,"bbox":[12,44,260,420],"direction":"left","track_id":7,"approach":{"track_id":7,"growth_rate":0.12,"positive_growth_frames":2,"approaching":true,"urgent_candidate":false,"level_hint":"APPROACH","spatial":{"area_ratio":0.31,"image_band":"near_candidate","focus_region":true}}}],"tracker":"botsort","model_status":"ready","timestamp":"2026-01-01T00:00:00Z"}
{"type":"obstacle_alert","device_id":"qiming_hat_001","direction":"front","object":"person","confidence":0.95,"vibration":"both","voice_text":"前方有行人，请注意","alert_state":"URGENT","track_id":7,"approach_reason":"central_target_area_increasing","timestamp":"2026-01-01T00:00:00Z"}
```

检测结果中的 `track_id`、`approach` 和 `tracker` 是新增的观测字段，原有类别、置信度、框坐标和方向字段保留。协议 2 帽端还会将连续两帧确认的新物品播报一次，并在目标离开画面约 3 秒后允许重新播报；语音队列对紧急告警设有更高优先级。自动预警只有在连续帧满足接近趋势时才产生：`APPROACH` 使用方向振动，`URGENT` 使用双侧振动并播报“请注意”；目标消失或趋势停止后发送 `vibration: "stop"` 的清除事件。图像区域和面积变化仅用于演示规则，不代表真实物理距离或碰撞时间。

## 语音消息

以下 JSON 样例属于旧 `/ws/device` 规划格式。当前帽端播放使用独立 `/ws/tts` WebSocket + `TS` 帧头，单声道 PCM S16LE 以 START/CHUNK/END 发送，帽端回传开始、完成、错误和取消回执。完整字段、错误码与帧长见 [TTS 音频下行](tts_audio_delivery.md)。麦克风仍使用 `/ws/audio` 上行，不和播放帧混用。

设备上传音频帧后，服务端发送：

```json
{"type":"asr_result","text":"前面有什么","request_type":"environment_query","timestamp":"2026-01-01T00:00:00Z"}
{"type":"voice_reply","text":"前方检测到一名行人","audio_format":"pcm_s16le","sample_rate":16000,"channels":1,"audio_seq":7,"timestamp":"2026-01-01T00:00:00Z"}
```

旧协议样例中的 `voice_reply.audio_format` 与 `frame_type=2` 下行描述不是当前播放实现。当前设备播报使用独立 `/ws/tts` 与 TS 二进制帧；麦克风输入仍走 `/ws/audio`，不在同一 WebSocket 混用上行和下行。

## 错误码

`AUTH_FAILED` 身份校验失败；`BAD_FRAME` 二进制头/长度错误；`UNKNOWN_DEVICE` 未注册设备；`MODEL_UNAVAILABLE` YOLO/FunASR/TTS 未就绪；`STALE_DETECTION` 环境缓存已过期；`QUEUE_FULL` 服务端队列已满；`COMMAND_TIMEOUT` 设备未确认；`INVALID_CONFIG` 参数校验失败。
