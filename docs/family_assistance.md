# 家属远程协助与安全监护

第一版复用现有帽端 WebSocket、`camera_frames` 帧缓存、设备心跳和 BeaconDB 粗定位，不让家属端直接连接 ESP32。

## 角色与授权

佩戴者账号在平板“家属远程协助”页输入已注册的家属账号和帽端设备编号，创建绑定。第一位绑定者成为该设备的佩戴者；其他账号不能抢占设备。绑定默认授予实时画面、位置和文字消息权限，佩戴者可以在数据库/API 中删除绑定。

家属端所有请求都需要 Bearer 会话，并通过 `device_family_bindings` 逐设备检查权限。每次查看帧和发送消息会写入 `family_access_logs`。摄像头帧仍由后端短时缓存后转发，过期帧返回 `410`。

## V1 接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/family/bindings` | 当前账号可访问的设备绑定 |
| `POST /api/family/bindings` | 佩戴者授权已注册家属账号；可选 `can_view_camera`、`can_view_location`、`can_send_messages` 布尔权限 |
| `DELETE /api/family/bindings/{id}` | 佩戴者撤销绑定 |
| `GET /api/family/overview` | 设备在线、摄像头、麦克风、位置和最近预警 |
| `GET /api/family/cameras/{device_id}/frame` | 授权后的最新 JPEG 帧 |
| `GET/POST /api/family/messages` | 文字协助消息；同时广播给已连接客户端并向帽端发送 `FAMILY_MESSAGE:` 文本 |

位置返回 `available/status/latitude/longitude/accuracy_m/updated_at`，并在界面标明这是 Wi‑Fi / BeaconDB 粗略估算，不等同 GPS。V1 没有轨迹回放、电子围栏或复杂的人身安全判断；设备状态只能表示连接和上报状态。
