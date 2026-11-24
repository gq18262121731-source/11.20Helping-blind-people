# 启明帽后端自动发现方案

## 目标

帽端只保存 Wi‑Fi 凭据和设备 ID，不再保存某一台电脑的固定 IP。电脑更换、DHCP 地址变化或后端重启后，ESP32 能在同一局域网内重新找到运行中的启明后端，并连接摄像头 WebSocket。

自动发现只解决“同一局域网内找到后端”。它不能绕过 Wi‑Fi 密码错误、访客网络隔离、AP 客户端隔离、不同 VLAN 或 Windows 防火墙阻止组播/广播的问题。

## 依据和开源实现

采用标准 DNS‑SD over mDNS 作为主发现机制：

- [RFC 6762 — Multicast DNS](https://www.rfc-editor.org/rfc/rfc6762.html)：定义无传统 DNS 服务器时的本地链路名称解析。mDNS 使用本地链路组播，适合没有固定基础设施的局域网。
- [RFC 6763 — DNS-Based Service Discovery](https://www.rfc-editor.org/rfc/rfc6763.html)：定义通过 `_service._tcp.local`、PTR、SRV 和 TXT 记录发现服务的方式。
- [Espressif Arduino ESPmDNS Web Server 示例](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESPmDNS/examples/mDNS_Web_Server/mDNS_Web_Server.ino)：官方示例使用 `MDNS.begin()` 和 `MDNS.addService()` 发布服务。
- [Espressif Arduino ESPmDNS-SD Extended 示例](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESPmDNS/examples/mDNS-SD_Extended/mDNS-SD_Extended.ino)：官方示例使用 `MDNS.queryService()`、`MDNS.address()` 和 `MDNS.port()` 查询服务。
- [ESP-IDF mDNS 文档](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/protocols/mdns.html)：Espressif 对 mDNS 的定位是本地网络服务和主机发现。
- [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf)：纯 Python 的 mDNS/DNS‑SD 发布和发现实现，不依赖 Windows Bonjour 或 Linux Avahi；其官方 [registration 示例](https://github.com/python-zeroconf/python-zeroconf/blob/master/examples/registration.py)展示了 `ServiceInfo`、`register_service()` 和 `unregister_service()`。

当前后端使用 Python 3.10，因此依赖应先固定为 `zeroconf==0.149.0`（该版本要求 Python >=3.10）；不要直接使用当前要求 Python 3.11 的最新版。

## 选定的网络协议

### 主路径：DNS‑SD/mDNS

后端启动后发布：

```text
服务类型：_qiming._tcp.local.
默认端口：8765
实例名：Qiming Backend <短主机标识>._qiming._tcp.local.
```

TXT 元数据建议包含：

```text
service=qiming-backend
protocol=2
camera_path=/ws/camera
device=esp32s3_cam_001
priority=10
```

SRV 记录提供端口，A 记录提供一台电脑当前可达的 IPv4 地址。ESP32 查询服务后，读取所有候选地址，按 `priority`、地址可达性和实例名稳定排序，尝试建立摄像头 WebSocket。

### 备用路径：UDP 局域网发现

部分手机热点、访客 Wi‑Fi 或企业 AP 会阻止 UDP 5353 组播。为保持换电脑能力，后端同时监听一个独立的 UDP 发现端口，例如 `8766`：

```text
ESP32 -> 255.255.255.255:8766
QH_DISCOVER/1 device=esp32s3_cam_001 nonce=<8-byte-random>

Backend -> ESP32 source address:source port
QH_BACKEND/1 nonce=<same> port=8765 priority=10
```

响应只接受同一请求的随机 nonce，避免使用网络中较早的旧响应。后端从收到请求的本地 socket 返回，不把 WSL、VPN 或其他虚拟网卡地址硬编码进回复。UDP 只是发现，不承载视频；视频仍使用已有 WebSocket。

### 最后回退：本地静态配置

保留 `BACKEND_HOST` 作为离线回退和工厂调试开关。自动发现成功后，运行时地址优先于它；自动发现失败时才尝试静态地址，并按退避间隔继续发现。这样不会因为历史 IP 失效而永久卡死。

## 后端改动范围

新增 `backend/discovery.py`，负责：

1. 选择适合发布的本地 IPv4 地址，默认排除回环、链路本地、WSL/VPN 虚拟接口；允许 `QIMING_DISCOVERY_INTERFACE` 显式指定网卡。
2. 启动 `Zeroconf`，注册 `_qiming._tcp.local.` 服务和 TXT 记录。
3. 监听 UDP `8766` 查询并按请求源地址回复。
4. 每 10 秒检查有效网卡地址；地址变化时原子注销旧 mDNS 服务并注册新地址。
5. 在 FastAPI shutdown 中注销服务、关闭 UDP 传输和 Zeroconf，避免重启留下陈旧服务记录。

`backend/config.py` 增加以下可选配置：

```text
DISCOVERY_ENABLED=true
DISCOVERY_SERVICE=qiming
DISCOVERY_UDP_PORT=8766
DISCOVERY_PRIORITY=10
DISCOVERY_INTERFACE=
```

服务注册必须与 `settings.port` 保持一致，不能把 8765 写成独立常量。服务 TXT 不放 Wi‑Fi 密码、API 密钥或用户数据。

## 固件改动范围

新增 `backend_discovery.h` 或拆分为独立 `.h`，不改变现有图像编码和 ACK 逻辑：

1. `#include <ESPmDNS.h>` 和 `#include <WiFiUdp.h>`。
2. 用 `IPAddress backendAddress`、`uint16_t backendPort` 代替只读的 `BACKEND_HOST` 字符串；保留 `BACKEND_PORT` 作为默认端口。
3. Wi‑Fi 连接后调用 `MDNS.begin()`，查询 `MDNS.queryService("qiming", "tcp")`。
4. 查询成功后使用官方 API 读取 `MDNS.address(i)`、`MDNS.port(i)` 和 TXT 元数据；只接受 `service=qiming-backend`、`protocol=2` 的结果。
5. mDNS 无结果时发 UDP nonce 查询；收到合法响应后使用响应地址和端口。
6. 每次连接失败、Wi‑Fi 重连或连续 ACK 超时都回到“重新发现”状态，使用 1、2、4、8、16、30 秒退避，避免阻塞摄像头主循环。
7. 发现成功后调用现有 WebSocketsClient 的 `begin(IPAddress, port, path)` 重载；当前 WebSockets 2.7.2 已提供该 API，因此无需把 IP 转成临时 C 字符串。
8. 摄像头连接成功后才启动高频图像发送。

串口日志增加可诊断信息：

```text
Discovery: mDNS query _qiming._tcp.local.
Discovery: found 192.168.x.x:8765 instance=...
Discovery: UDP fallback found 192.168.x.x:8765
Discovery: connecting camera...
Discovery: backend unavailable; retry in 8 s
```

## 安全边界

mDNS 和 UDP 发现只证明“同一局域网中有服务”，不能作为身份认证。第一阶段应：

- 只发布端口、协议版本、路径和设备组信息；
- 对 WebSocket 保持现有设备 ID 校验；
- 不在发现报文中携带 Wi‑Fi 密码或模型配置；
- 记录发现到的后端地址和最后一次连接结果；
- 后续若需要防止同网段误连接，再增加 `DISCOVERY_TOKEN`/设备配对令牌，并在 WebSocket 握手时校验，不把令牌放进公开 TXT。

## 实施顺序

1. 后端先加入 mDNS 注册和 UDP 发现监听，增加单元测试验证多网卡筛选、nonce、端口和服务注销。
2. 固件加入 mDNS 查询，但保留静态 `BACKEND_HOST` 回退；先只打印发现结果，不改变连接目标。
3. 固件切换为发现地址连接，验证 `/ws/camera` 能出现 `esp32s3_cam_001`，再验证 `/ws/tts` 和 `/ws/audio`。
4. 在两台不同电脑上分别启动后端，轮换 DHCP 地址，验证无需修改固件即可连接。
5. 在普通路由器、手机热点和 AP 客户端隔离环境各做一次验收；组播被拦截时应明确落到 UDP fallback 或提示网络隔离，而不是显示“摄像头无响应”。
6. 最后才删除或隐藏静态 IP 配置，保留调试开关和故障诊断日志。

## 验收标准

- 电脑 A 和 ESP32 同一 Wi‑Fi，电脑 A IP 改变后，ESP32 在 30 秒内重新连接。
- 换到电脑 B，只启动同一项目和后端，ESP32 无需重新烧录即可连接。
- 后端重启不会产生重复服务或错误的旧地址。
- 页面 `/api/health` 的 `devices` 出现 `esp32s3_cam_001`，页面选择帽端来源后能看到新帧。
- 摄像头连接失败时，TTS 和音频不会继续连接到旧电脑地址。
- mDNS 被阻止时，UDP fallback 可发现；两种发现都失败时，串口能给出明确原因和下一次重试时间。

## 当前项目对应点

- 固件目前在 `esp32s3_gray_camera.ino` 中直接使用 `webSocket.begin(BACKEND_HOST, BACKEND_PORT, CAMERA_WS_PATH)`，TTS 也使用相同的固定地址。
- 后端已有 `/ws/camera`，接受 `protocol=2&format=jpeg`，收到 JPEG 后返回 `ACK`，因此自动发现只需替换连接目标，不需要改变视频帧协议。
- `config.local.h` 仍可保留 Wi‑Fi 凭据；新的自动发现不解决 Wi‑Fi 凭据轮换，换路由器/热点时仍需重新配置 Wi‑Fi。
