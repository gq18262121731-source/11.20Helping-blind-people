# API

- `GET /api/v1/guard/devices` / `POST /api/v1/guard/devices`
- `POST /api/v1/location/report`, `GET /api/v1/location/latest`, `GET /api/v1/location/history`
- `GET /api/v1/events?device_id=...` (device-authorized recent position, warning and sharing events)
- `POST /api/v1/location/provider` (owner-only explicit provider switch)
- `POST /api/v1/location/simulate/start|stop` (developer diagnosis flow only)
- `GET /api/v1/map/config`, `POST /api/v1/map/reverse-geocode`, `POST /api/v1/map/nearby`, `POST /api/v1/map/walking-route`, `POST /api/v1/map/assistant`
- `POST /api/v1/map/search` (`device_id`, `keyword`, optional `city`; uses the gateway's `/v3/place/text` and returns normalized POIs)
- `POST /api/v1/map/voice/transcribe` (uses the existing 16 kHz ASR adapter; returns an editable transcript)
- `POST /api/v1/map/voice/speak` (queues a confirmed reply on the existing hat TTS path)
- `GET /api/v1/navigation/status`, `POST /api/v1/navigation/cancel`
- `POST /api/v1/sharing/start|stop`
- `GET /api/v1/video/mjpeg`
- `WS /ws/v1/guard/{device_id}`（建立后发送 authenticate 消息）

API 和视频均检查 Bearer 会话或同源会话 Cookie，并检查 device_family_bindings。一个设备同一时刻只能使用一个已登记定位来源。只有明确启用 `QIMING_MAP_DEMO_MODE` 的隔离诊断环境才会返回带 `mode=mock` 的地图演示数据；默认配置在地图服务不可用时返回 503。

当前地图查询走 CocoMap 中转的高德通道，服务端使用独立中转鉴权；不转发用户登录 Token。`map/config` 仅返回可公开的 JS SDK 配置和服务是否配置的布尔值，绝不返回中转密钥。没有 JS SDK Key 不阻断服务端搜索，但交互底图仍不可用。
