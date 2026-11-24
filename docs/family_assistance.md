# 家属远程协助与安全监护

第一版复用现有帽端 WebSocket、`camera_frames` 帧缓存、设备心跳和 BeaconDB 粗定位，不让家属端直接连接 ESP32。

## 角色与授权

佩戴者账号可先在“家属远程协助”页点击“添加我的帽端”，登记设备编号并查看自己的设备。后续输入已注册的家属账号进行授权。第一位登记或绑定者成为该设备的佩戴者，其他账号不能抢占设备。家属绑定默认授予实时画面、位置和消息权限，佩戴者可以撤销授权。

家属端所有请求都需要 Bearer 会话，并通过 `device_family_bindings` 逐设备检查权限。画面查看按时间段记录，留言发送写入 `family_access_logs`。摄像头帧仍由后端短时缓存后转发，过期帧返回 `410`。

## V1 接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/family/devices` | 当前账号登记自己的帽端，仅需 `device_id`，重复登记返回同一记录 |
| `GET /api/family/bindings` | 当前账号可访问的设备绑定 |
| `POST /api/family/bindings` | 佩戴者授权已注册家属账号；可选 `can_view_camera`、`can_view_location`、`can_send_messages` 布尔权限 |
| `DELETE /api/family/bindings/{id}` | 佩戴者撤销绑定 |
| `GET /api/family/overview` | 设备在线、摄像头、麦克风、位置和最近预警 |
| `GET /api/family/cameras/{device_id}/frame` | 授权后的最新 JPEG 帧 |
| `GET/POST /api/family/messages` | 查看和发送协助消息，按设备授权读取，并返回本次帽端播报入队结果 |
| `POST /api/family/voice/transcribe?device_id=...` | 上传录音并识别为待确认文字，校验该设备的消息权限 |

位置返回 `available/status/latitude/longitude/accuracy_m/updated_at`，并在界面标明这是 Wi‑Fi / BeaconDB 粗略估算，不等同 GPS。V1 没有轨迹回放、电子围栏或复杂的人身安全判断；设备状态只能表示连接和上报状态。

## 家属画面与语音留言

电脑控制台新增“家属协助”入口，手机 / 平板 App 使用“家属远程协助”入口。当前账号可先添加自己的帽端开始联调，再授权其他家属账号访问。家属登录同一个电脑后端后查看已授权设备。设备编号需要与实际帽端编号一致，登记离线设备不会改变其真实在线状态。

自己的设备记录返回 `is_owner_device: true`，不作为家属授权展示或撤销。设备登记与邀请通过事务锁保护归属检查。

打开已授权设备后，客户端约每 700 ms 串行获取一张最新 JPEG，网络请求较慢时不会重叠排队。设备、授权与消息状态独立刷新。暂停画面、切换设备、离开页面或进入后台会停止或取消画面请求。画面过期或失去权限时清除旧图，避免把冻结画面当作当前环境。

语音留言采用以下流程。

1. Android 点击“语音留言”，允许麦克风权限，录制一段协助内容。
2. 点击结束录音，或等待说话后的静音结束，由电脑识别为文字，再检查并按需修改。
3. 点击确认发送，后端优先读取本地音频；未收录时由 Edge TTS 异步合成并缓存 PCM，再通过专用 TTS 通道发送给帽端播放。

Android 直接录制 16 kHz、16 bit、单声道 WAV，并交由电脑的本地 SenseVoice / sherpa-onnx 模型识别。录音和识别可取消，录音不会写入留言历史，确认文字后才发送。此路径适配本机小米语音服务已停止语音搜索的情况。

电脑浏览器使用其支持的系统语音识别接口，在不支持的浏览器或非安全地址下显示提示，仍可输入文字。

本机语音识别使用 `C:\Anaconda\envs\yolov8\python.exe`，依赖 `sherpa-onnx`。本地 `backend/.env` 中的 `ASR_MODEL`、`ASR_TOKENS` 分别指向 `D:/development/qiming-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/` 下的 `model.int8.onnx` 和 `tokens.txt`。更换电脑后需准备模型并调整本地配置。录音接口在模型未配置、忙碌或识别失败时会返回明确错误。

此版本发送的是确认后的文字，由帽端合成声音。家属本人原声传输及连续音频通话需要额外的帽端音频接收、解码和输出能力。平板本机朗读与物理帽子扬声器分别验证。

## 播报状态

发送消息时可附带 `message_type: "text"` 或 `"voice_transcript"`。后者表示用户确认后的语音识别文字。`body` 为 1–500 字文字，帽端当前只接受 GBK 可编码字符。长消息会拆分为每段不超过 240 GBK 字节的播报命令，紧急障碍物预警优先于家属留言。

发送响应中的 `delivery` 包含 `status` 和 `detail`。

| 状态 | 含义 |
| --- | --- |
| `queued` | 已加入帽端发送队列，尚未确认扬声器实际出声 |
| `offline` | 消息已保存，帽端离线，本次未播报 |
| `unsupported` | 当前连接或协议不支持远程合成播报 |
| `speech_disabled` | 帽端语音播报开关已关闭 |
| `queue_full` | 消息已保存，播报队列已满，需稍后重新发送 |

离线消息在设备重连后不会自动播放。设备概览中的 `speech` 返回当前可用性与具体原因。现有帽端协议没有扬声器实际播放完成回执，因此 `queued` 不作为“已播放”使用。

家属消息内容通过逐设备授权的 HTTP 接口读取，不广播到全局网页 WebSocket。连续查看画面时，按账号和设备每 30 秒记录一次访问日志。
