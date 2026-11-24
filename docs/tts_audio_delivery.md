# 本地语音库与帽端 TTS 下行

## 目标与边界

稳定高频语句可在准备阶段由 Edge TTS 批量生成 WAV。运行时先按完整回复文字查本地缓存；命中后直接传 PCM，未命中则异步调用 Edge TTS、FFmpeg 转为 PCM WAV，写入本地缓存后再发送。重复内容命中缓存，不再实时请求合成服务。生成/缓存资产后，语音播放只依赖本地文件。

该通道与现有 `/ws/camera` 摄像头/马达连接、`/ws/audio` 麦克风 PCM 上行分离。摄像头和马达逻辑不等待 TTS。Wi-Fi 仍共享带宽，音频分块按播放速率发送，并限制每个设备的待播队列。

后端把 Edge TTS 合成限制为每个设备最多 4 个等待任务、全局同时合成 2 个；单句不超过 500 字/30 秒。语音缓存上限为 512 条或 128 MiB，并保留仍在播放或队列中的 WAV。语音回执会更新任务的 `tts_status`。

ESP32 `tts_player.h` 使用独立 TTS 网络任务、有限音频块队列及 FreeRTOS I2S 播放任务。摄像头与马达仍使用原协议/连接。当前 `config.local.h` 还没有 MAX98357A 的 BCLK、LRCLK、DIN 引脚；未配置时固件拒绝播放并回传错误，禁止将此状态当作真机可播。

## 生成本地语音资产

1. 编辑 `config/tts_phrases.json`，为高频语句填写唯一、稳定的小写 `phrase_id` 和中文 `text`。
2. 设定 `voice` 与 `sample_rate`。默认 voice 为 `zh-CN-XiaoxiaoNeural`，默认采样率为 16 kHz。
3. 部署电脑安装 `python -m pip install -r backend/requirements-tts.txt`，并确认 FFmpeg 已在 `PATH` 或将 `TTS_FFMPEG` 指向可执行文件。
4. 在仓库根目录运行 `python tools/generate_tts_library.py`，批量生成高频语音。动态回复则由后端运行时自动合成并缓存。

本地 WAV 和 manifest 位于 `backend/data/tts/library/`，已由 `.gitignore` 排除。`config/tts_phrases.json` 是高频固定短语清单。首次合成每条动态回复时需要电脑联网访问 Edge TTS；WAV 缓存生成后，重复播放不需要访问 Edge TTS。

## 后端接口

- `GET /api/tts/library`：登录后查看本地语音库和当前 TTS 设备连接。
- `POST /api/tts/browser-ticket` + `GET /api/tts/browser-stream/<ticket>`：电脑音箱播放入口。命中 PCM 库时回传 WAV；未命中时返回一次性 60 秒 URL，直接流式播放 Edge TTS MP3，并在播放期间由 FFmpeg 转成 16 kHz PCM 写入共享缓存。
- `POST /api/tts/synthesize-browser`：登录后直接返回共享缓存中的 Edge TTS PCM WAV，供需要整段资源的客户端使用。
- `POST /api/tts/speak`：登录后传 `{ "device_id": "esp32s3_cam_001", "text": "需要播报的完整回复", "priority": 1 }`，或使用 `phrase_id`。缓存命中返回 `queued`；未命中返回 `synthesizing`，合成后自动加入设备队列。
- `POST /api/tts/stop`：登录后按 `device_id` 取消合成/等待任务、清除待播队列并停止当前播放。
- `POST /api/voice-vision/tasks`：实机来源默认由前端选择 `tts_target=device`，回复后自动进入同一 TTS 队列；“重新朗读”使用任务 `/speak` 路由。
- `WS /ws/tts?device_id=<设备 ID>`：帽端专用播放连接；每个设备有独立优先队列，最多 16 条。连接在当前可信局域网使用，不应直接暴露公网。

电脑摄像头、电脑麦克风、上传图片和电脑端地图页面使用浏览器票据播放 Edge TTS 流；帽端画面、帽端麦克风回复、障碍告警和家属留言只进入 `/ws/tts`。同一事件只选择一个出口，网页不再把帽端播报失败静默改成本机原生语音。

语音库 manifest 在每次查询/下发时按修改时间重新加载。音频文件必须处于语音库目录内、与 SHA-256 一致，且是 8–48 kHz、单声道、16-bit PCM；单句最长 30 秒。当前 ESP32 固件的 MAX98357A I2S 输出固定为 16 kHz，因此传给帽端的语音库和实时合成采样率必须与 16 kHz 一致，否则后端会拒绝下发。传输时读取并校验 WAV，线上只发送样本数据，不发送 WAV 文件头。

## 二进制协议 v1

所有多字节整数都是 little-endian。每个 WebSocket 二进制消息是一帧；同一 request 的 WebSocket 顺序即音频顺序。

| 帧 | 字节布局 | 方向 | 含义 |
|---|---|---|---|
| START | `TS`、版本 u8、类型 u8、request_id u32、sample_rate u32、total_pcm_bytes u32 | 后端→帽端 | 建立播放会话；接收端准备好环形缓冲/I2S 后返回 PLAYBACK_STARTED。`total_pcm_bytes=0xFFFFFFFF` 表示 Edge TTS 流式长度未知，END 时按实际字节数收尾 |
| CHUNK | `TS`、版本 u8、类型 u8、request_id u32、sequence u32、PCM 数据 | 后端→帽端 | 目前固定 40 ms 一块，PCM 必须 16-bit 样本对齐 |
| END | `TS`、版本 u8、类型 u8、request_id u32、chunk_count u32 | 后端→帽端 | PCM 已完整发送；帽端播完后返回 PLAYBACK_DONE |
| CANCEL | 与 END 相同的 12-byte 控制头，value=0 | 后端→帽端 | 停止当前播放并清空当前 request 缓冲 |
| READY / PLAYBACK_STARTED / PLAYBACK_DONE / PLAYBACK_ERROR | 相同的 12-byte 控制头；value 为状态或错误码 | 帽端→后端 | 状态回执；READY 使用 request_id=0 |

PLAYBACK_CANCELLED 使用同一 12-byte 状态头。I2S 配置缺失、采样率不匹配、序号错误、队列溢出、音频写入失败会回报错误码。

`TS` 之后的类型值由 `backend/tts_protocol.py` 定义。START 是 16 字节；CHUNK 与控制帧头是 12 字节。流式会话先发送 START，再以不超过播放速率的节奏发送 PCM 块；固件播放队列有界，累计音频限制 8 MiB。代码和测试是当前协议的权威定义。

## 下一步

软件端到端测试可运行 `python -m pytest tests/test_tts_library.py tests/test_tts_protocol.py tests/test_tts_delivery.py tests/test_generate_tts_library.py tests/test_tts_end_to_end.py -q`。模拟 ESP32 播放端可运行 `python tools/tts_hat_simulator.py --device-id esp32s3_cam_001`；`--play` 只经电脑声卡试听，不代表帽端喇叭验收。真机下一步是核对并填写三条 I2S GPIO，编译烧录，再同时测试摄像头 ACK、马达、实际 TTS 播放和播放回执。
