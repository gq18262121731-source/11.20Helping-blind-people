# 地图服务

当前服务端默认使用 CocoMap 中转的高德通道。`MAP_SERVICE_MODE=cocomap`、`COCOMAP_BASE_URL=https://map.culture09.xyz`，密钥填写 `COCOMAP_API_KEY`。这不是百度 AK，也不是官方高德 Web Key。

已核对中转站公开服务目录（2026-11-23）：

- 逆地理编码：`GET /v3/geocode/regeo`
- 关键字搜索：`GET /v3/place/text`
- 周边搜索：`GET /v3/place/around`
- 步行路线：`GET /v3/direction/walking`

服务端携带 `Authorization: Bearer <中转Key>`、`X-Cocomap-Provider: amap`；请求参数保留高德协议，但不发送 `key` 查询参数。只接受 `ok=true` 且内部高德 `status=1` 的 JSON。401/402/429、非 JSON、错误包装和上游业务失败返回不可用，不重试扣费查询、不悄悄改走其他服务。禁止在日志或浏览器配置中返回中转密钥。

坐标内部仍按 WGS-84 存储，查询前转换为 GCJ-02（经度在前，最多六位小数），POI 和路线结果转回 WGS-84，避免因改中转造成位置偏移。

网页交互底图仍是独立能力，当前选择百度地图 JS API GL：设置 `MAP_JS_PROVIDER=baidu`，并把百度开放平台浏览器端应用的 AK 配置到 `BAIDU_MAP_JS_AK`。浏览器 SDK 由访问者的浏览器直接加载，所以 AK 是可公开使用的浏览器 Key，应按控制台要求限制来源域名。加载时应直接请求完整的 `/getscript?type=webgl` SDK；`/api?v=1.0&type=webgl` 返回的是使用 `document.write` 的引导脚本，动态插入时 SDK 可能尚未初始化。该中转公开目录仅含 REST/静态地图能力，不包含交互底图 SDK；中转 Key 不能替代浏览器端 AK。缺少 AK 时不会使用静态图冒充可缩放、可拖动地图。

如需改用官方高德网页 SDK，设置 `MAP_JS_PROVIDER=amap`，配置 `AMAP_JS_KEY` 与 `AMAP_JS_SECURITY_CODE`。无论选择哪个交互底图，地址搜索和步行路线都可继续走 CocoMap 中转。

只有在隔离课堂/自动化环境显式设置 `QIMING_MAP_DEMO_MODE=true` 时，后端才允许返回带 `mode=mock` 的测试地图数据；真实验收保持 `false`。关键字搜索不提供假数据回退。

公开契约来源：
- https://map.culture09.xyz/?provider=amap&api=place-text
- https://map.culture09.xyz/api/meta?provider=amap
- https://map.culture09.xyz/api/services?provider=amap
- https://map.culture09.xyz/api/services/place-text
