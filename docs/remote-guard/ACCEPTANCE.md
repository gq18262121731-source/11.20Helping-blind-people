# 验收记录（2026-11-24）

## 本次中转适配

PASS：已读取用户指定 CocoMap 站点的公开服务目录及 `place-text`、`place-around`、`geocode-regeo`、`walking` 契约。服务端改用 Bearer 中转密钥、`X-Cocomap-Provider: amap`、原始 `/v3/...` 路径，解析 `ok/data` 包装。未把后台登录接口当作地图接口，也没有用文档示例地点填充页面。

PASS：`conda run -n yolo-blind python -m pytest -q tests/test_remote_guard.py`：32 passed，1 个依赖弃用警告。包含设备授权、模拟位置隔离、来源切换、地图服务不可用、百度浏览器 AK 配置、四条中转路径、错误包装/401/402/429处理、密钥不下发、关键字地点搜索和缺失距离不伪造。

PASS：本轮 `node --check frontend/remote-guard.js` 和 `node --test tests/*.test.cjs`：85 passed。`conda run -n yolo-blind python -m py_compile backend/remote_guard.py` 通过。本次未修改 OCR 或原摄像头语音队列实现。

PASS：2026-11-23 已在当前后端进程读取本地 `COCOMAP_API_KEY`，真实中转请求返回了逆地理编码地址、3 条周边 POI、3 条关键字搜索结果和 1 条步行路线。密钥未进入 URL、浏览器配置或日志。

PASS：百度 JS API GL 浏览器 AK 已配置。排查发现 `/api?v=1.0&type=webgl` 仅返回带 `document.write` 的引导脚本；原代码动态插入后立即检查 SDK 全局对象，因此页面显示“地图 SDK 未就绪”。已改为直接加载 `/getscript?type=webgl` 的完整 SDK 和样式。`localhost:8765` 与 `127.0.0.1:8765` 两个 Referer 下，SDK 和 CSS 均返回 HTTP 200，SDK 内容含 `BMapGL` 与 `Convertor`，AK 值未进入输出。真实浏览器地图瓦片显示仍待登录后确认。

PASS：设备所有者进入远程守护页后自动尝试本机浏览器定位；仅本机地址和所有者会触发，浏览器权限拒绝时不会启动位置来源，位置变更、停止和页面卸载均有清理。家属可见范围沿用设备共享设置。

PASS：针对“附近有没有医院”添加确定性周边查询路由，直接调用 `place/around`，不让可选 LLM 将附近搜索改成普通地点搜索。地图助手开始查询前等待自动定位首次上报，避免定位刚启动就把旧位置发给搜索接口。后端地图专项 34 passed，前端 87 passed。

PASS：浏览器本地冒烟打开登录页，页面有内容且 0 条控制台错误。该浏览器会话未登录，未进入远程守护页或触发真实浏览器定位；实际位置权限需要设备所有者首次进入时在浏览器中授权。

## 当前工作树全量回归

未通过：`python -m pytest -q` 本轮结果为 188 passed、13 failed、18 errors、3 warnings（约 47 秒）。失败集中在 `tests/test_family.py`、`tests/test_raw_camera.py` 对旧语音队列成员的期望：`SPEECH_SECONDS_PER_CHARACTER`、`_speech_delay`、`_speech_queue` 等缺失，以及帽端播报可用状态不匹配。此次中转适配未更改这些实现或测试；不能沿用前轮 195 passed 宣称当前工作树全绿。

## 页面与硬件边界

已观察：当前工作树两个独立一级页面可打开；无设备时地图助手输入、录音和快捷查询禁用；有绑定但无位置时显示真实空状态。真实中转查询已在后端验证；百度交互底图端点已确认可访问，真实浏览器瓦片仍待登录后确认。CocoMap 继续提供逆地理编码、POI 和步行路线 REST 查询，不参与百度底图 SDK 加载。

NOT_TESTED：最终工作树 320/375/414/768px 逐尺寸验收、真实手机局域网访问、真实帽端视频连续播放、GNSS、YOLO/ASR/TTS 端到端及 30 分钟硬件稳定性。未据桌面小屏或模拟数据宣称 PASS。
