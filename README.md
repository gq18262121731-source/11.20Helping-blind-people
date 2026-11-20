# 启明帽（QimingHat）

帽载式视障环境感知预警装置，配套“启明智能陪护管理平台”。项目采用 ESP32-S3 CAM 采集端、Windows / Python AI 服务端和浏览器管理端的分层架构，也可以直接使用电脑摄像头进行软件演示。

当前已有登录注册、YOLO 物体检测、检测结果展示、所选摄像头中文播报、拍照识字与朗读、文本对话、记录查询和 Excel 导出等实现。ESP32-S3 的图像上传和 YOLO 识别人已真机跑通；GC0308 已切换到 RGB565 彩色采集、软件 JPEG 上传，用户已确认彩色显示正常，并保留灰度回退配置。马达输出暂时禁用，完整语音输入、播放链路仍需进一步联调，不应视为已经完成实物验收的辅助出行产品。

## 功能与实现状态

| 功能 | 当前状态 |
| --- | --- |
| 用户注册、登录、验证码、退出登录 | 已实现；密码使用 PBKDF2 哈希，SQLite 保存用户和会话 |
| 电脑摄像头检测 | 浏览器采集 JPEG，后端 YOLO 检测，页面绘制检测框 |
| 网页画面来源选择 | 支持电脑本机与帽端画面；保留所选来源，帽端离线时等待重连，不自动打开电脑摄像头 |
| 方位判断 | 按检测框中心判断左侧、前方和右侧，不提供真实距离 |
| 接近趋势预警 | YOLOv8n 后接 Ultralytics BoT-SORT（失败时 ByteTrack / predict 回退），按图像空间面积趋势进入 NORMAL、APPROACH、URGENT；不等同真实距离或碰撞时间 |
| 摄像头中文播报 | 跟随所选画面来源，使用浏览器语音合成，支持物品名称翻译、合并播报、冷却和取消 |
| 拍照识字与朗读 | 截取所选来源的当前画面，Qwen3-VL 转录文字，浏览器中文分段朗读；支持取消与重播 |
| 环境查询 | 根据近期检测缓存回答，默认超过 15 秒的缓存不用于回答 |
| 普通文本问答 | 调用兼容 OpenAI 格式的接口，可连接本机 Ollama |
| 记录与配置 | 预警、交互记录查询，障碍物记录 Excel 导出，参数保存 |
| 系统总览 | 今日统计、近 7 天预警趋势、模块状态、最近预警与交互；模拟来源单独标明 |
| 无硬件模拟 | 模拟设备上线、心跳、JPEG 上传和明确标记的模拟检测结果 |
| ESP32 固件 | GC0308 320×240 RGB565 彩色采集、软件 JPEG / WS 上传，保留灰度模式；彩色显示已确认，基础 YOLO 链路已真机验证。支持 ACK / 心跳 / 重连，马达输出禁用，长期稳定性及后续马达接线待验收 |
| 完整语音交互 | FunASR、后端 TTS 和设备音频收发尚未接通；软件联调接口接收文本 |
| Android 平板 App | Flutter 客户端，含登录、总览、本机 / 电脑网页摄像头画面选择、当前画面识字、平板 TTS、实时检测、设备与设置；真机声音和相机需设备联调 |
| 家属远程协助与安全监护 | V1 已接入 Flutter 家属页：设备绑定、实时帽端画面、Wi-Fi 粗略位置、在线/摄像头/麦克风状态、文字协助和访问日志；所有家属接口要求登录并按绑定权限校验 |

## 技术栈与数据流

- 后端：Python、FastAPI、Uvicorn、SQLite、HTTP / WebSocket。
- 检测：OpenCV、NumPy、Ultralytics YOLOv8，默认权重 `yolov8n.pt`。
- 对话：HTTP 兼容接口，安装示例使用 Ollama 的 `qwen3-vl:4b`。
- 前端：原生 HTML / CSS / JavaScript，无需 npm 构建；摄像头和播报分别使用浏览器媒体接口及语音合成接口。
- 固件：Arduino-ESP32、WebSockets、ArduinoJson。

```text
电脑摄像头 ──HTTP JPEG──┐
                      ├─→ FastAPI → YOLO → 跟踪 → 接近趋势状态机 → 检测缓存 / 预警 / SQLite
ESP32 摄像头 ──WS──────┘              └─→ WebSocket → 网页检测框 / 中文播报

文本问题 → 环境查询：读取检测缓存
         → 普通问答：调用配置的语言模型接口
```

家属端使用同一套后端会话。佩戴者登录平板后，在“家属远程协助”页选择“我是佩戴者，授权家属”，填写已注册的家属账号和帽端设备编号；家属登录自己的账号后即可看到已授权设备。摄像头帧仍由后端从帽端缓存转发，家属端不会直接连接 ESP32。位置展示明确标为 Wi-Fi / BeaconDB 粗略估算，无法替代 GPS。

普通问答请求只发送文本，环境查询使用 YOLO 检测缓存。新增的拍照识字接口会把当前拍摄的图片发送给配置的视觉模型，用于转录文字。

## 项目目录

```text
QimingHat/
├── backend/
│   ├── main.py                # 页面、HTTP API、WebSocket 和启动事件
│   ├── services.py            # YOLO、预警、检测缓存、对话和消息广播
│   ├── ocr.py                 # 独立图片识字服务，可供后续硬件链路复用
│   ├── overview.py            # 总览数据库统计与模型服务状态检查
│   ├── auth.py                # 验证码、密码哈希和登录会话
│   ├── database.py            # SQLite 表结构与数据库访问
│   ├── config.py              # 环境变量和运行参数
│   ├── requirements.txt       # 后端运行依赖
│   ├── requirements-conda.txt # 现有 Conda 环境依赖，保留兼容的视觉库版本
│   ├── .env.example           # 配置模板
│   └── models/                # YOLO 权重默认存放位置
├── frontend/                  # 实际由后端提供的管理页面和登录页面
│   ├── index.html
│   ├── login.html
│   ├── login.js
│   ├── camera-monitor.js       # 网页本机 / 帽端来源选择、预览和检测展示
│   ├── camera-speech.js        # 所选摄像头来源的物品中文播报
│   ├── ocr-reader.js           # 拍照识字、分段朗读和取消控制
│   ├── overview.js             # 总览折线图、模块状态和最近记录
│   └── overview.css            # 总览布局与响应式样式
├── firmware/                  # ESP32-S3 Arduino 固件
├── tablet_app/                # Flutter Android 平板客户端与测试
├── tools/simulator.py         # 无硬件设备模拟器
├── tests/                     # Python 测试与 Node.js 播报测试
├── scripts/                   # Windows 启动、停止和环境检查脚本
├── docs/                      # 架构、安装、接线和协议说明
├── models/README.md           # 模型下载说明；不是默认权重目录
├── qiming-login/              # 独立登录页面及集成说明副本
└── README.md
```

## 环境要求

Android 平板工具链、USB / 局域网运行和 APK 打包命令见 [平板端开发与启动说明](docs/tablet.md)。电脑后端启动并连接 Android 设备后，在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action check
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action run -DeviceId "你的设备ID"
```

App 填写 `http://电脑IPv4:8765`，Android 模拟器填写 `http://10.0.2.2:8765`。平板与网页共用账号，AI 在电脑运行，识字声音由平板本机 TTS 播放。

- Windows，以下命令使用 PowerShell。
- 本项目使用 Conda 的 `yolo-blind` 环境，当前本机 Python 版本为 3.10.22。
- Chrome 或 Edge；真实摄像头演示需要允许摄像头访问，并准备可用的中文语音和声音输出。
- 首次安装依赖、下载 YOLO 权重或 Ollama 模型需要网络。
- 普通文本问答和拍照识字需要配置对应的模型服务；使用本机模型时安装 Ollama。YOLO 物体检测和浏览器语音合成本身不依赖 Ollama。
- Node.js 仅用于运行前端播报测试，不是网页运行所必需。

## 首次安装与启动

以下命令均以 `QimingHat` 为项目根目录。请先在终端进入该目录；如果当前位于 `Morning Star`，执行 `cd .\QimingHat`。

### 1. 使用 Conda 环境并安装依赖

```powershell
# 在 Anaconda Prompt 或已初始化 Conda 的 PowerShell 中执行
conda env list
conda activate yolo-blind
python -c "import sys; print(sys.executable)"

# 保留已有兼容版本的 PyTorch、YOLO、OpenCV 和 NumPy，补齐运行依赖
python -m pip install -r backend\requirements-conda.txt

# 不覆盖已有本机配置
if (-not (Test-Path backend\.env)) {
    Copy-Item backend\.env.example backend\.env
}
```

本机环境解释器为 `D:\Anaconda\envs\yolo-blind\python.exe`。无需再创建 `backend/.venv`；旧目录不会被启动脚本使用。其他电脑若尚无同名环境，可先执行 `conda create -n yolo-blind python=3.10 -y`，再激活并安装依赖。`requirements.txt` 保留原有固定版本清单，当前 Conda 环境优先使用 `requirements-conda.txt`，避免不必要的视觉库降级。

不想激活环境时，可用 `conda run --no-capture-output -n yolo-blind python ...` 代替下面的 `python ...`。如果 PowerShell 的 `conda` 命令异常，可改用 Anaconda Prompt；本机也可用 `& D:\Anaconda\Scripts\conda.exe run --no-capture-output -n yolo-blind python ...`。

如需使用项目根目录的 `build_handoff.py` 生成 Word 文档，请在同一 `yolo-blind` 环境中额外安装工具依赖：

```powershell
python -m pip install -r requirements-tools.txt
```

安装包名为 `python-docx`，代码中使用 `from docx import ...` 导入。该依赖仅用于文档工具，启动后端无需安装。

### 2. 准备 YOLO 权重

默认检查文件 `backend/models/yolov8n.pt`。如果该文件已存在，可跳过下载。否则执行：

```powershell
New-Item -ItemType Directory -Force backend\models | Out-Null
Push-Location backend\models
try {
    python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
} finally {
    Pop-Location
}
```

也可以在 `backend/.env` 中将 `YOLO_WEIGHTS` 设置为已有权重的绝对路径。权重缺失或初始化失败时，服务会报告模型不可用，不会伪造真实检测结果。

### 3. 启动后端与网页

先进入包含 `backend` 文件夹的 `QimingHat` 项目目录，再启动服务。默认使用支持 ESP32 / 平板连接的监听地址，本机可直接复制以下命令（其他电脑请替换第一行的项目路径）：

```powershell
cd "C:\Users\67028\Desktop\Morning Star\QimingHat"
conda activate yolo-blind
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

如果终端还停在 `Morning Star>`，直接运行启动命令会报 `ModuleNotFoundError: No module named 'backend'`；请先执行 `cd .\QimingHat`，使终端路径以 `QimingHat>` 结尾。

启动后在电脑浏览器打开 <http://127.0.0.1:8765/>。监听 `0.0.0.0` 时，本机网页和局域网设备可同时访问。仅做电脑独立演示、不接 ESP32 或局域网平板时，才使用以下仅限本机的启动命令：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

设备连接地址填写电脑当前的局域网 IP，可用 `ipconfig` 查看；更换 Wi-Fi 或热点后需重新确认 IP。`0.0.0.0` 仅用于监听，不要作为浏览器或 App 的连接地址。

也可以使用启动脚本，它会通过 Conda 明确选择 `yolo-blind`，不依赖当前激活环境，也不会回退到其他 Python。终端需能够找到 Conda，或已设置有效的 `CONDA_EXE`：

```powershell
.\scripts\start.bat
```

检查解释器、运行依赖和包依赖一致性：

```powershell
.\scripts\check_environment.bat
```

启动后打开：

- 登录入口：<http://127.0.0.1:8765/>
- 管理页面：<http://127.0.0.1:8765/dashboard.html>（先注册并登录）
- 健康检查：<http://127.0.0.1:8765/api/health>
- API 文档：<http://127.0.0.1:8765/docs>

前后端由同一服务提供，不需要单独启动前端或直接打开 HTML 文件。停止服务时，在运行它的终端按 `Ctrl+C`。

开发时可以在上述 Uvicorn 命令末尾添加 `--reload`。修改 `.env` 后建议手动重启。启动脚本固定使用 `0.0.0.0:8765`；需要更改地址或端口时，请直接调整 Uvicorn 命令参数，不能仅修改 `.env` 中的 `HOST` / `PORT`。

### 4. 选择摄像头来源并验证播报

1. 注册账号并登录管理页面。
2. 进入“实时环境监测”，在顶部统一的“摄像头”下拉框选择来源。电脑画面选择“默认电脑摄像头”或具体设备（如 `Integrated Camera`），点击“启动电脑摄像头”，允许浏览器访问。
   帽端画面选择“ESP32 帽端摄像头”，点击“查看帽端画面”；对应设备 ID 为 `esp32s3_cam_001`，须先通过 Wi-Fi 连接后端并上传图像。该选项在设备未连接时也会保留，标注“等待画面”，不表示已在线。
3. 保持“识别播报”勾选，观察检测框、物品名称和方位。
4. 点击“测试播报”确认中文语音及声音输出。
5. 在“硬件调试 → 软件联调”输入“前面有什么？”等问题，测试检测缓存查询。

同一物品和方位默认每 10 秒最多播报一次，多种物品会合并播报。停止摄像头、关闭播报或退出登录会取消对应播报。环境问题的回答取决于实际检测结果，不表示路径安全或真实距离。

网页会记住所选来源，刷新设备列表也会保留电脑摄像头的选中项。帽端离线、超时或图像过期时会清除旧画面并提示等待，不会自动调用电脑摄像头；检测结果、物品播报和识字均跟随所选来源。首次更新此功能后，请重启后端并在网页按 `Ctrl+F5` 强制刷新，无需重新烧录帽端或打包平板 App。

### 5. 拍照识字并通过电脑朗读

此功能需要下方的 Ollama 视觉模型配置，不需要安装额外的 OCR 引擎。现有 `yolo-blind` 环境已具备所需依赖。

1. 确保 Ollama 正在运行且已下载 `qwen3-vl:4b`，核对 `backend/.env` 中的 `OPENAI_BASE_URL`、`OPENAI_MODEL` 等配置。
2. 更新代码后刷新网页并登录，在“实时环境监测”顶部控制区选择并启动画面来源，等待画面出现。
3. 对准书本或标牌，在同一顶部控制区点击“识字并朗读”。每次点击只提交一张照片，不持续识字；识别结果显示在画面下方。
4. 页面显示识别原文，并通过电脑扬声器分段朗读。浏览器如阻止自动朗读，点击“重新朗读”。
5. 点击“停止识字 / 朗读”可取消当前等待或语音；停止摄像头、切换摄像头、退出登录同样会取消。取消等待后，Ollama 已开始的任务可能仍需处理到完成或超时，短时间内再次提交可能提示忙碌。

识字及朗读期间会暂停普通物品名称播报，YOLO 检测仍继续运行；结束后恢复普通播报。系统参数中的语音总开关同时控制文字朗读，摄像头旁的“识别播报”只控制物品播报。

拍照最长宽度为 1600 像素，接口支持不超过 6 MB、1200 万像素的 JPEG / PNG。首次模型加载可能较慢；长页面建议分区域拍摄。识别结果可能漏字、错字，应与原文核对；输出因长度限制截断时会提示，并暂停自动朗读。

电脑网页继续使用浏览器语音合成；新固件增加 LU6288 串口转发，可将后端障碍物播报文本经 ESP32 UART 输出。LU6288 已通过 CH340 单测，并确认 ESP32 GPIO21 可驱动模块播报。`backend/ocr.py` 的识字服务独立于摄像头和音频输出，网页识字接口需要登录 Bearer 令牌。

## 系统总览

首页上方显示设备连接、今日预警、今日交互和中文检测模式。下方按左宽右窄排列预警趋势、模块运行状态、最近预警和最近交互；摄像头及识字操作继续集中在“实时环境监测”。

- 今日计数和近 7 天趋势从 SQLite 全量记录汇总，不受记录列表默认 100 条限制；按浏览器时区统计，接口默认 `Asia/Shanghai`。
- 趋势区分实际采集与模拟设备。设备 ID 以 `simulator_` 开头的预警标为模拟来源；交互记录中的模拟 ASR / TTS 状态也会标为软件联调。
- 模块状态分别显示后端、YOLO、对话 / 识字模型、本地摄像头和帽子设备。帽子连接以当前设备会话为准，不依赖数据库中可能过期的在线标记。
- 对话 / 识字模型通过配置接口的 `/models` 列表检查，显示“模型已安装”表示接口可达且列表包含所配置模型，不等于已完成一次实际推理。检查结果缓存 20 秒，不会触发模型加载。
- 最近预警与交互各显示最新 5 条，点击“查看全部”进入对应模块；没有数据时显示空状态。
- 总览每 10 秒自动刷新；连接失败会明确提示，已有数据保留并标为上次更新数据。首次加载失败时不会用零值替代未知统计。

新增总览接口和静态资源后需要重启后端，再刷新网页。

## 启动本地 Ollama 对话与识字（使用这些功能时需要）

YOLO 物体检测与 Ollama 识字使用不同服务。仅启动 8765 后端或看到 ESP32 画面，
不代表 11434 上的识字模型服务已运行。先打开 Ollama 应用，或在另一个终端启动服务
并保持运行；如果已有 Ollama 服务，无需重复启动：

```powershell
ollama serve
```

另开终端检查已安装模型；已有 `qwen3-vl:4b` 时无需重新下载：

```powershell
ollama list
# 仅在模型不存在时执行
ollama pull qwen3-vl:4b
```

也可通过 `http://127.0.0.1:11434/api/tags` 确认服务可访问且模型在列表中。
2026-11-19 现场排查发现 Ollama 未运行；启动后，以包含“启明帽文字识别测试”和
“安全出行 12345”的测试图片调用项目实际识字服务，两行均正确返回，模型已加载后的
该次请求耗时约 1.7 秒。该结果只验证模型服务，不代表 ESP32 小字识别准确率。

在 `backend/.env` 中配置：

```dotenv
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3-vl:4b
LLM_TIMEOUT_SECONDS=300
```

重启项目后，在“硬件调试 → 软件联调”输入“你好，请介绍你能提供的帮助”等普通问题验证。首次请求可能需要加载模型。环境类问题仍优先走检测缓存，不经过普通对话模型。未配置模型或调用失败时，接口会返回降级提示。

## 可选：无硬件模拟联调

在 `backend/.env` 中设置并重启后端：

```dotenv
SIMULATION_MODE=true
```

另开 PowerShell，进入项目根目录，执行：

```powershell
conda activate yolo-blind
# uvicorn[standard] 会带入 websockets
python tools\simulator.py
```

模拟器默认连接 `ws://127.0.0.1:8765/ws/device`，设备 ID 为 `simulator_001`。后端对 `simulator_` 前缀设备生成明确标记为 `simulation` 的固定模拟检测结果，用于验证设备连接、心跳、图像上传、结果推送和预警记录链路。

需要自定义连接地址或设备 ID 时，在模拟器所在终端设置：

```powershell
$env:QH_DEVICE_WS = "ws://127.0.0.1:8765/ws/device"
$env:QH_DEVICE_ID = "simulator_001"
python tools\simulator.py
```

该模式不能作为真实识别验收。前端本地摄像头播报会忽略模拟结果；真实检测时将 `SIMULATION_MODE` 改回 `false` 并重启。

## 配置说明

配置文件位于 `backend/.env`，由 `backend/config.py` 加载。建议先复制 `.env.example`，再按本机环境修改。

| 配置项 | 说明 |
| --- | --- |
| `HOST` / `PORT` | 配置对象中的监听设置；常用 Uvicorn 启动方式以命令行参数为准 |
| `YOLO_WEIGHTS` | 权重路径；相对路径以 `backend/` 为基准，默认 `models/yolov8n.pt` |
| `YOLO_CONFIDENCE` | 初始检测置信度，默认 `0.35` |
| `DETECTION_CACHE_TTL_SECONDS` | 环境查询缓存有效期，默认 `15` 秒 |
| `DATABASE_PATH` | SQLite 路径；相对路径以服务启动时的工作目录为基准 |
| `OPENAI_BASE_URL` | 兼容接口地址，例如 `http://127.0.0.1:11434/v1` |
| `OPENAI_API_KEY` | 对话接口密钥；不要提交真实密钥 |
| `OPENAI_MODEL` | 对话模型名称 |
| `OCR_ENGINE` | 默认 `rapidocr`；兼容回退仅在 RapidOCR 无文字且配置旧视觉接口时触发 |
| `ASR_MODEL` / `ASR_TOKENS` | sherpa-onnx SenseVoice 模型与词表路径；transducer 模型可改用 `ASR_ENCODER` 等字段 |
| `LLM_TIMEOUT_SECONDS` | 对话请求超时；模板为 `300` 秒，未配置时代码默认为 `120` 秒 |
| `SIMULATION_MODE` | 是否为模拟设备启用模拟检测，默认 `false` |
| `DEVICE_TOKEN` | 预留设备令牌；当前设备 WebSocket 尚未校验，不构成有效访问保护 |

注意：

- `.env.example` 中的 `DATABASE_PATH=data/qiming_hat.db` 在从项目根目录启动时对应根目录下的 `data/`；完全不配置该项时，代码默认使用 `backend/data/qiming_hat.db`。为避免出现两份数据库，请固定从项目根目录启动并核对配置。
- 数据库首次启动时自动建表。用户、会话、设备、预警、交互、系统参数和系统日志都保存在 SQLite 中；备份时请先停止服务并复制实际使用的数据库文件。
- 页面保存过的置信度和方位阈值会在后端启动时从数据库恢复，可能覆盖 `.env` 的初始值。
- 当前部分开关和参数只完成保存：后端预警冷却仍固定为 2 秒，障碍物及语音开关尚未完整接入服务端业务逻辑。

## ESP32 固件准备

当前已跑通灰度采集的 ESP32-S3 CAM，优先使用
[摄像头硬件联调说明（含彩色 / 灰度）](docs/esp32s3_gray_camera.md) 和
`firmware/esp32s3_gray_camera/esp32s3_gray_camera.ino`。
后端使用 `0.0.0.0:8765`，接口 `/ws/camera` 兼容 76,800 字节原始灰度帧，
优化固件默认使用已确认的 GC0308 RGB565 彩色采集，在 ESP32 上软件压缩为 JPEG 后通过
`format=jpeg` 上传，减少网络负载；设置 `CAMERA_COLOR_MODE=0` 可回到灰度采集。
适配现有 YOLO、二进制电机指令及平板预览。

帽端固件每约 2 分钟扫描附近 Wi-Fi 热点并估算粗略位置，首次扫描约在联网后 30 秒开始；
监测页选择 ESP32 帽端后可查看坐标、服务估算误差和更新时间。该方式不是 GPS，精度取决于
BeaconDB 对周围热点的覆盖；后端会把热点 BSSID（MAC 地址）和 RSSI 发送到 BeaconDB，
不会发送 Wi-Fi 名称或密码。接入细节与限制见 [ESP32 Wi-Fi 粗略定位](docs/wifi_geolocation.md)。

当前仅调试摄像头，不需要连接马达。后端启动后，可在 `yolo-blind` 环境运行
`python tools\watch_camera.py --seconds 30`，直接观察帽端的真实检测结果、方位及连接重置情况。

2026-11-19 本机对比：原始灰度方案在 35 秒窗口收到 18 帧（约 0.51 FPS）；
压缩灰度首轮约 3.96 FPS；默认彩色模式一分钟内上传 170 帧，稳定上传阶段约 3.13 FPS，
平均 JPEG 约 10.3 KB，用户已确认颜色正常。不同窗口受场景与网络影响，不能视为严格 A/B；
彩色测试仍出现最长约 4.6 秒的网络写入延迟，不能宣称卡顿完全消除。详细记录见联调文档。

随后固件改为复用 PSRAM JPEG 缓冲区，并将 WebSocket 帧头和图片合并写入。
2026-11-19 真机 60 秒复测上传并完成真实识别 240 帧（约 4 FPS），TCP 写入最长 171 ms，
最长检测间隔 422 ms；结果仅代表本次设备、网络和场景，详细数据见联调文档。

**马达和麦克风接线与当前固件：** GPIO10/11 是摄像头 D3/D0，GPIO4/5 是 SCCB，不能复用。
当前 MAX9814 的模拟 `OUT` 接 GPIO1（ADC1）；左、右马达驱动 `IN` 分别接 GPIO40/41；`L/R/B` 分别驱动左、右、双侧，
单次 300ms。两个带 MOS 驱动的模块从 ESP32 单个 5V 脚并联供电并共地；两路启动
最大约 180mA，若供电不足导致相机或 Wi-Fi 重启，应使用独立 5V 电源。麦克风电平诊断已确认 GPIO1 能检测到声音变化；硬件录音上传和 ASR 尚未实现。

下面步骤属于原有 `/ws/device` JPEG 模板，与新的灰度示例是两套独立入口：

1. 在 Arduino IDE 安装 ESP32 Arduino Core、`ArduinoJson` 和 `WebSockets` 库。
2. 打开 `firmware/firmware.ino`，选择与实物匹配、带 PSRAM 的 ESP32-S3 开发板。
3. 修改 `firmware/config.h` 中的 Wi-Fi、电脑局域网地址、端口和设备 ID。
4. 对照实际板卡原理图核对全部摄像头、麦克风、马达及串口引脚，不能直接假定现有 GPIO 映射适用于所有板卡。
5. 烧录后以 115200 波特率打开串口，检查摄像头初始化和网络连接。

电脑与设备需在可互通的网络中，并按需允许 Windows 防火墙放行后端端口。固件中的 `BACKEND_HOST` 应填写电脑局域网 IP，不能填 `127.0.0.1`。

原有 JPEG 模板仍存在以下联调边界：

- 后端自动预警通过 `/ws/camera` 向帽端发送方向指令和 LU6288 `<G>` GBK 文本；GPIO21 帽端播报已真机确认，振动马达仍禁用。
- 音频采样已有接口，但主循环尚未触发音频上传，后端也尚未接通完整音频识别与播放。
- 设备上线、断线重连及外设状态仍需实机验证，不应把代码中的状态字段当作硬件自检结果。

## 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 后端与模型状态 |
| GET | `/api/overview?tz=Asia/Shanghai` | 登录后获取总览统计、趋势、最近记录和模块状态；时区可选 |
| GET / POST | `/api/auth/captcha`（GET）、`/api/auth/register`、`/api/auth/login`（POST） | 验证码、注册与登录 |
| GET / POST | `/api/auth/session`（GET）、`/api/auth/logout`（POST） | 会话校验与退出 |
| POST | `/api/local-camera/frame` | 上传本地摄像头 JPEG，支持 `seq` 查询参数 |
| GET | `/api/tablet/cameras` | 登录后获取可选摄像头来源和是否有新鲜画面 |
| GET | `/api/tablet/cameras/{source_id}/frame` | 登录后读取所选来源的最新 JPEG；来源停止或画面过期返回 410 |
| POST | `/api/ocr/read` | 登录后上传 JPEG / PNG 原始图片，返回 `text`、`has_text`、`truncated` 和模型信息 |
| GET | `/api/devices` | 设备记录 |
| GET | `/api/obstacles`、`/api/voices` | 预警和交互记录 |
| GET / PUT | `/api/config` | 读取和保存配置 |
| GET | `/api/export/obstacles.xlsx` | 导出障碍物记录 |
| POST | `/api/simulate/voice` | 输入文本测试环境查询或普通问答 |
| WebSocket | `/ws/web` | 网页结果推送与控制消息 |
| WebSocket | `/ws/device` | 设备上线、心跳和二进制图像上传 |
| WebSocket | `/ws/camera` | ESP32 原始 QVGA 灰度图兼容入口；新固件加 `protocol=2` 启用 ACK 和 B/S 指令 |
| WebSocket | `/ws/audio` | 16kHz/16bit/Mono PCM 会话；`audio_start`、二进制 PCM、`audio_end` |
| POST | `/api/simulate/audio` | 上传 WAV 文件验证 sherpa-onnx 中文 ASR（无需 ESP32） |

完整接口参数可在服务启动后查看 `/docs`。协议文档包含规划内容；当前二进制实现采用 `QH` 标识的 26 字节固定头，联调时应以 `backend/main.py`、`firmware/websocket_manager.h` 和 `tools/simulator.py` 的一致实现为准。

### 纯软件语音联调

第一阶段不需要连接 ESP32。安装 `backend/requirements-conda.txt` 中的 RapidOCR、ONNX Runtime
和 sherpa-onnx，并在 `.env` 配置中文 ASR 模型文件后，可用
`python tools/audio_simulator.py sample.wav` 上传 WAV；模拟器和固件共用 `/ws/audio` 的
PCM/QH 帧定义。语音任务按 `LISTENING → RECORDING → RECOGNIZING → PROCESSING → COMPLETED`
状态运行，每个设备同时只处理一个会话；ASR、OCR 失败都会恢复监听，视觉检测继续独立运行。

## 测试

从项目根目录运行。`pytest` 不在运行依赖清单中，需要单独安装：

```powershell
conda activate yolo-blind
python -m pip install pytest
python -m pytest tests -q
```

Python 测试覆盖方位判断、设备断线清理、验证码与会话、二进制头布局、灰度帧转换与震动指令适配、重连竞争、平板预览，以及图片识字的鉴权、图片校验、模型请求和错误处理。前端播报测试使用 Node.js 内置测试工具，无需 `npm install`：

```powershell
node --test tests\camera_monitor.test.cjs tests\camera_speech.test.cjs tests\ocr_reader.test.cjs tests\overview.test.cjs
```

软件阶段最终验收可由单一入口执行（自动准备依赖、下载素材和模型、运行真实 OCR/ASR、启动后端及模拟器、执行完整回归并生成报告）：

```powershell
.\tools\run_software_acceptance.ps1
```

报告输出到 `docs/SOFTWARE_ACCEPTANCE_REPORT.md`；下载的模型、素材和运行日志位于被 Git 忽略的本地目录。

测试通过不等于完成摄像头、模型、Ollama 或 ESP32 的端到端验证，相关能力仍需按上面的步骤分别检查。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| `No module named ...` | 运行 `python -c "import sys; print(sys.executable)"`，确认使用 `yolo-blind`；再用该环境的 `python -m pip install -r backend/requirements-conda.txt` 补齐依赖 |
| 模型显示 `unavailable` | 查看 `/api/health` 的 `model_error`，确认权重路径和依赖；修复后重启 |
| 页面能打开但无法启动摄像头 | 优先通过本机 `http://127.0.0.1:8765/` 访问，检查浏览器权限、系统隐私设置及摄像头占用；局域网 HTTP 访问可能受浏览器安全上下文限制 |
| 能看到画面但没有识别结果 | 检查后端输出、模型状态、摄像头帧上传与网页 WebSocket 连接 |
| ESP32 一直显示“等待画面” | 查看 `/api/health` 的 `devices` 是否包含 `esp32s3_cam_001`；若为空，先检查帽端供电、同一网络及后端监听地址。使用 `scripts\start.bat` 启动，固定监听 `0.0.0.0:8765`；仅监听 `127.0.0.1` 时帽端无法接入。设备已连接但仍无画面时，检查串口发送 / ACK 和图像是否已过期 |
| 有检测结果但没有声音 | 点击“测试播报”，检查“识别播报”、系统音量、中文语音和浏览器声音权限 |
| 环境查询提示无法获取可靠信息 | 先启动摄像头并等待检测，缓存默认只保留 15 秒有效期 |
| 普通问答返回降级提示 | 检查 Ollama 是否运行、模型是否下载、接口配置是否正确，以及后端系统日志 |
| 识字失败或超时 | 先检查 `http://127.0.0.1:11434/api/tags` 是否可访问；无法连接时打开 Ollama 或运行 `ollama serve` 并保持运行。模型已安装无需重新下载。再检查 `OPENAI_MODEL` 支持图片输入；保持画面清晰、减少单次文字量，首次加载稍候再试 |
| 识字完成但没有声音 | 检查系统语音总开关，点击“重新朗读”，确认浏览器声音权限与中文语音可用 |
| 更改配置后没有变化 | 重启后端；监听端口需修改启动命令，部分检测参数还会受数据库保存值影响 |
| 端口 8765 被占用 | 停止旧服务或在 Uvicorn 命令中换端口，并同步修改设备、模拟器的连接地址 |

## 使用边界与后续工作

- 本项目当前适合本地或可信局域网演示。管理页面有登录检查，但业务 API 和 WebSocket 的鉴权尚未完整覆盖，CORS 也较宽松，不应直接暴露到公网。
- 目前所有设备共用容量为 1 的图像队列，优先处理最新帧；多设备隔离、慢连接处理和完整协议校验仍需完善。
- YOLO 在电脑后端运行；ESP32 负责采集、传输和执行指令，不运行 AI 推理。浏览器本地摄像头播报由浏览器完成。
- 普通目标检测不等于距离测量，检测不到物体也不代表道路安全，系统不能替代盲杖、导盲犬或专业出行辅助。
- 后续重点：硬件自动预警闭环、完整 ASR / TTS、参数实际生效、接口鉴权、设备重连和实物验证。

## 相关文档

- [安装与运行说明](docs/installation_guide.md)
- [系统架构设计](docs/system_architecture.md)
- [硬件接线说明](docs/hardware_wiring.md)
- [ESP32-S3 灰度摄像头联调](docs/esp32s3_gray_camera.md)
- [ESP32 Wi-Fi 粗略定位](docs/wifi_geolocation.md)
- [通信协议](docs/communication_protocol.md)
- [模型准备说明](models/README.md)
- [独立登录页面集成说明](qiming-login/LOGIN_INTEGRATION.md)

部分设计文档描述的是目标架构或规划功能；当前实现状态以本 README 的说明和源码为准。
