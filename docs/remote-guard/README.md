# 远程守护与 AI 地图助手

远程守护复用现有登录、设备绑定、最新帧缓存、YOLO 事件、中文 ASR 和帽端语音队列。地图与视频优先展示；模拟来源只在开发者诊断入口控制。没有有效位置、地图服务或新鲜画面时，家属页面显示不可用，不以图片或虚构地点填充。

## 配置当前中转站（CocoMap）

在 `backend/.env` 填写已有字段（不要重复添加同名配置）：

```dotenv
MAP_SERVICE_MODE=cocomap
COCOMAP_BASE_URL=https://map.culture09.xyz
# 在中转站自己的控制台创建的 sk_coco_... 密钥，仅在后端使用
COCOMAP_API_KEY=
QIMING_MAP_DEMO_MODE=false
```

基础地址填写站点根地址，不填文档页的 `?provider=amap&api=place-text`，不追加 `/v3`、`/api` 或 `/v3/place/text`。当前按用户给定页面使用中转站的高德通道：`Authorization: Bearer <中转Key>` 和 `X-Cocomap-Provider: amap`。后端处理 `{ok:true,data:{...}}` 包装，继续向家属页面返回同样的业务字段。地点、位置与路线请求会经过所配置中转站。

已接入逆地理编码、关键字地点搜索、周边搜索和步行路线规划。中转密钥缺失、401/402/429、网络失败、包装失败或上游业务失败均不会被当作成功，也不会偷偷切回官方直连。需要中转站 Key 对应权限和额度；填写后重启后端再验证真实结果。

### 网页底图

当前交互底图改用百度地图 JS API GL。中转站密钥与地图 SDK AK 是不同凭据：

```dotenv
MAP_JS_PROVIDER=baidu
BAIDU_MAP_JS_AK=
AMAP_JS_KEY=
AMAP_JS_SECURITY_CODE=
```

在百度开放平台创建浏览器端 JS API GL 应用并启用 WebGL 地图服务，把取得的 AK 填入 `BAIDU_MAP_JS_AK`。按百度控制台要求设置来源白名单。本机验收时允许 `localhost`/`127.0.0.1`；手机联调时也要允许实际访问的域名或 IP。中转 Key 留在 `COCOMAP_API_KEY`，只由后端使用。

内部位置为 WGS-84。页面调用百度官方 `BMapGL.Convertor` 转成百度 BD-09 坐标后，才绘制当前位置、精度圈、搜索标记与路线。未配置百度 AK 时，地址、地点与路线仍可由后端中转查询，底图则如实显示不可用。

如果改用官方高德网页 SDK，设置 `MAP_JS_PROVIDER=amap` 并配置高德 JS Key 和对应安全密钥。REST 查询仍可继续走中转。

本地仅检查配置是否存在，不应在日志、截图或聊天中打印密钥值。保存后重启后端，浏览器刷新后再验证真实底图、实际地址、POI 与路线。`QIMING_MAP_DEMO_MODE` 只在隔离自动化测试/课堂环境显式开启；真实验收保持关闭。

## 启动

从项目根目录运行：

```powershell
Set-Location -LiteralPath 'F:\学校\工坊\导盲帽子\Morning-Star'
conda run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

如果从其他目录运行，显式指定 `--app-dir`：

```powershell
conda run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --app-dir 'F:\学校\工坊\导盲帽子\Morning-Star' --host 0.0.0.0 --port 8765
```

也可运行 `scripts/start.bat`，脚本会自动切换项目目录。`ModuleNotFoundError: No module named 'backend'` 应先检查工作目录或 `--app-dir`，不需要安装一个名叫 backend 的包。

本机访问 `http://127.0.0.1:8765`；手机访问 `http://电脑局域网IPv4:8765`。同源视频地址自动带当前设备编号，不将回环地址写死在家属页面。真实手机的相机、麦克风权限还受浏览器安全上下文限制，桌面小屏检查不能代替手机实测。

## 定位与语音

设备所有者进入远程守护页后会自动请求运行浏览器的电脑定位权限；成功后坐标同步为当前设备的位置。浏览器首次仍需允许定位，家属查看权限沿用设备共享设置。它不代表 GNSS 硬件。设备同一时间只有一个有效位置来源；切换来源清空旧导航，轨迹限于同一来源的采集区间。位置过期时停止实时展示。

地图语音输入调用项目现有 ASR，先显示可编辑转写，用户确认提交后再查询。回复优先入帽端现有播报队列；队列不可用时使用现有浏览器播报实例，并分别显示状态，排队成功不表示硬件已经播放。

验收结果见 `ACCEPTANCE.md`。缺少 Key 或真实硬件的项目必须保持 BLOCKED / NOT_TESTED，不能用自动化替身宣称真实链路 PASS。
