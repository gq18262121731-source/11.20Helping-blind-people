# 安全

所有 API 需要登录；位置、视频、导航按设备绑定授权。共享可由佩戴者暂停。家属端不会接收模拟位置或演示路线。MJPEG 使用同源 qh_token Cookie，避免把长期 Bearer token 放在 URL。外部地图 Web Key 只从环境变量读取。

CocoMap 中转 Key 仅从 `COCOMAP_API_KEY` 读取并在后端 Bearer 请求头使用，不进入 URL、JS 配置或浏览器存储。中转请求固定使用 `X-Cocomap-Provider: amap`，不携带用户登录 Token 或其他服务的密钥。基础地址只接受管理员本地配置的 HTTPS 站点根地址，并禁用重定向，避免自动把鉴权转发到其他地址。
