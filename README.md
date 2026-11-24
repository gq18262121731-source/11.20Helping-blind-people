# 启明帽（QimingHat）

帽载式视障环境感知预警装置，配套“启明智能陪护管理平台”。项目采用 ESP32-S3 CAM 采集端、Windows / Python AI 服务端和浏览器管理端的分层架构，也可以直接使用电脑摄像头进行软件演示。

## GPU 环境与启动原则

页面显示 `CPU / GPU 未启用` 或 `显存不可用`，表示运行后端的 Python 进程没有看到 CUDA。常见原因是启动时用了另一套 Python、Conda 环境里安装了 CPU 版 PyTorch、NVIDIA 驱动不可用，或者启动脚本在 CUDA 检查失败后换到了别的环境。

项目不假设任何人的磁盘路径或 Conda 环境名称。先在 PowerShell 填写自己的变量，后续命令只使用变量：

~~~powershell
$ProjectRoot = '填写：包含 backend、frontend、scripts 的项目根目录'
$CondaEnv = '填写：你准备使用的 Conda 环境名，例如 qiming-gpu'
$CondaExe = (Get-Command conda -ErrorAction Stop).Source
Set-Location -LiteralPath $ProjectRoot
~~~

如果找不到 conda 命令，请打开 Anaconda Prompt，或把本机 conda.bat / conda.exe 的完整路径填入 `$CondaExe`。不要复制其他电脑的 C: 或 D: 路径。

### 1. 安装 NVIDIA 与 CUDA 版 PyTorch

- Windows 必须安装 NVIDIA 驱动；先运行 `nvidia-smi`，确认能看到显卡、驱动和显存。
- 建议使用 Python 3.10 或 3.11。
- 打开 [PyTorch 本机安装选择器](https://pytorch.org/get-started/locally/)，选择 Windows、Pip、Python 和当前驱动支持的 CUDA 版本，复制页面生成的安装命令。`backend/requirements-conda.txt` 不负责安装 CUDA 版 PyTorch，必须先完成这一步。
- 不要用不带 CUDA 说明的 `pip install torch` 覆盖已经可用的 CUDA 版。

~~~powershell
& $CondaExe create -n $CondaEnv python=3.10 -y

# 在 PyTorch 安装选择器生成的 pip 命令中，复制 --index-url 后面的 CUDA wheel 地址到这里。
$TorchIndexUrl = '粘贴选择器给出的 https://download.pytorch.org/whl/cu... 地址'
if ($TorchIndexUrl -notmatch '^https://download\.pytorch\.org/whl/cu\d+$') { throw '请填入 PyTorch 官方选择器生成的 CUDA wheel 地址' }
& $CondaExe run -n $CondaEnv python -m pip install torch torchvision --index-url $TorchIndexUrl

& $CondaExe run -n $CondaEnv python -m pip install -r (Join-Path $ProjectRoot 'backend\requirements-conda.txt')
~~~

如果 `$CondaEnv` 已经存在，跳过 `create`，但仍要确认里面安装的是 CUDA 版 PyTorch。官方选择器给出的命令若含有 CUDA wheel 参数，应保留这些参数；不要改成 PyPI 默认的 CPU wheel。

### 2. 启动前验证同一个解释器

~~~powershell
& $CondaExe run -n $CondaEnv python -c "import sys, torch; print('Python:', sys.executable); print('PyTorch:', torch.__version__); print('CUDA runtime:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'); assert torch.cuda.is_available()"
& $CondaExe run -n $CondaEnv python tools\environment_probe.py
& $CondaExe run -n $CondaEnv python -m pip check
~~~

所有检查都通过后再启动服务：Python 路径必须属于 `$CondaEnv`；`CUDA available` 必须是 `True`；GPU 必须显示实际显卡型号；`environment_probe.py` 中 YOLO 和深度模型文件必须存在；`pip check` 不能有依赖冲突。

### 3. 准备模型并配置路径

默认模型路径是项目相对路径，不依赖某台电脑的绝对目录：

- YOLO：`backend/models/yolov8n.pt`；
- Depth Anything V2 Small：由 `tools/prepare_depth_model.py` 按配置准备；
- YOLO 相对路径以 `backend/` 为基准；深度模型和模型代码相对路径以项目根目录为基准；绝对路径按本机实际位置填写。

~~~powershell
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
& $CondaExe run -n $CondaEnv python tools\prepare_depth_model.py
~~~

如果模型放在其他位置，只修改你自己的 `backend/.env` 中的 `YOLO_WEIGHTS`、`DEPTH_MODEL_PATH`、`DEPTH_CODE_PATH`。不要把个人电脑的绝对路径写入 README 或提交到仓库。

### 4. 启动与确认 GPU 状态

~~~powershell
$env:QIMING_ENV = $CondaEnv
$env:QIMING_CONDA = $CondaExe
$env:QIMING_ALLOW_CPU = '0'
& (Join-Path $ProjectRoot 'scripts\start.bat')
~~~

启动脚本会先检查 PyTorch 的 CUDA 状态；CUDA 不可用时直接退出，不会静默切换到 CPU 环境。也可以使用同一环境直接启动：

~~~powershell
& $CondaExe run --no-capture-output -n $CondaEnv python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
~~~

启动后检查真实运行状态：

~~~powershell
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 5
$spatial = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/spatial/status' -TimeoutSec 5
$health | ConvertTo-Json -Depth 6
$spatial | ConvertTo-Json -Depth 8
~~~

GPU 正常时，`/api/spatial/status` 应显示 `gpu.available=true`、实际 `gpu.name`、`depth.device=cuda`，通常 `depth.precision=fp16`；页面运行性能中的 GPU 名称和显存字段也应有值。YOLO 推理会明确指定 `cuda:0`。需要确认实际负载时，在另一个终端运行 `nvidia-smi -l 1`，再启动摄像头观察显存和利用率。FPS 低不等于 GPU 未启用，先以这些状态和 `nvidia-smi` 为准。

### 5. 仅在明确需要时使用 CPU

~~~powershell
$env:QIMING_ALLOW_CPU = '1'
& (Join-Path $ProjectRoot 'scripts\start.bat')
~~~

CPU 模式只用于无 NVIDIA 显卡、驱动故障或排查问题，会显著降低 YOLO 和深度速度。深度对照演示不要误用 CPU 模式。

## 快速启动

完成上面的 GPU 检查后，在同一个 PowerShell 会话中执行：

~~~powershell
Set-Location -LiteralPath $ProjectRoot
$env:QIMING_ENV = $CondaEnv
$env:QIMING_CONDA = $CondaExe
$env:QIMING_ALLOW_CPU = '0'
& (Join-Path $ProjectRoot 'scripts\start.bat')
~~~

服务启动后打开 `http://127.0.0.1:8765/`。先执行 `GET /api/health` 和 `GET /api/spatial/status`，确认 `gpu.available=true`、`depth.device=cuda` 后再启动摄像头。已经有服务监听 8765 时不要重复启动；在原终端按 Ctrl+C 停止后再重启。

### 运行依赖


- YOLO 权重默认放在 `backend/models/yolov8n.pt`；尚未准备时按上面的模型步骤下载。
- RapidOCR 和 ONNX Runtime 随 `backend/requirements-conda.txt` 安装，默认 `OCR_ENGINE=rapidocr`。
- Ollama 和 `qwen3-vl:4b` 仅供普通问答及视觉模型回退使用，不是 YOLO / 深度推理启用 GPU 的前提。
- 帽端语音输入还需配置 `sherpa-onnx` 模型文件；帽端语音输出使用独立 Edge TTS 音频链路。
- 不存在 `backend/.env` 时复制模板。模板数据库路径按启动目录解析；更改项目目录时请重新核对 `DATABASE_PATH`，避免读写到另一份数据库。

当前已有登录注册、YOLO 物体检测、检测结果展示、所选摄像头中文播报、拍照识字与朗读、文本对话、记录查询和 Excel 导出等实现。ESP32-S3 图像上传和 YOLO 识别已真机跑通；电脑 TTS 音频下行与模拟播放回执已端到端验证。I2S GPIO、固件编译/烧录和帽端扬声器试听尚未验收；麦克风到 ASR 的帽端上行也仍需联调。

## 功能与实现状态

| 功能 | 当前状态 |
| --- | --- |
| 用户注册、登录、验证码、退出登录 | 已实现；密码使用 PBKDF2 哈希，SQLite 保存用户和会话 |
| 电脑摄像头检测 | 浏览器采集 JPEG，后端 YOLO 检测，页面绘制检测框 |
| 网页画面来源选择 | 支持电脑本机与帽端画面；保留所选来源，帽端离线时等待重连，不自动打开电脑摄像头 |
| 方位判断 | 按检测框中心判断左侧、前方和右侧，不提供真实距离 |
| 接近趋势预警 | YOLOv8n 后接 Ultralytics BoT-SORT（失败时 ByteTrack / predict 回退），按图像空间面积趋势进入 NORMAL、APPROACH、URGENT；不等同真实距离或碰撞时间 |
| 深度风险预警 | 将同一 Track ID 的相对深度、前方关注区、目标框扩张趋势和辅助 TTC 融合，经过三帧平滑分为 L0/L1/L2/L3；深度无效时不伪造风险等级 |
| 摄像头中文播报 | 普通目标播报与深度风险播报分开；L1/L2/L3 都有风险语音，L3 可打断普通播报并优先播报 |
| 拍照识字与朗读 | 截取所选来源的当前画面，默认由 RapidOCR 识字，必要时调用配置的视觉模型回退，浏览器中文分段朗读，支持取消与重播 |
| 语音与识字工作台 | 单独页面复用帽端/电脑画面，使用现有意图识别；环境查询将同帧 YOLO 结果与 VLM 场景描述合成为回复，记录模型阶段和朗读状态 |
| VLM 场景描述 | 显式环境查询或场景描述时调用已配置的视觉模型，只返回场景描述；YOLO 目标与方位保持为结构化识别依据，VLM 不生成安全结论 |
| 环境查询 | 根据近期检测缓存回答，默认超过 15 秒的缓存不用于回答 |
| 普通文本问答 | 调用兼容 OpenAI 格式的接口，可连接本机 Ollama |
| 记录与配置 | 预警、交互记录查询，障碍物记录 Excel 导出，参数保存 |
| 系统总览 | 今日统计、近 7 天预警趋势、模块状态、最近预警与交互；模拟来源单独标明 |
| 无硬件模拟 | 模拟设备上线、心跳、JPEG 上传和明确标记的模拟检测结果 |
| ESP32 固件 | GC0308 320×240 RGB565 彩色采集、软件 JPEG / WS 上传，保留灰度模式；彩色显示已确认，基础 YOLO 链路已真机验证。支持 ACK / 心跳 / 重连，马达输出禁用，长期稳定性及后续马达接线待验收 |
| 语音链路 | Edge TTS 动态回复、PCM 缓存、独立 `/ws/tts` 与播放回执已接通；ESP32 MAX98357A I2S 固件已实现，GPIO 配置/烧录与真机试听未验收；麦克风 ASR 上行待硬件联调 |
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

网页、软件联调和 `/ws/audio` ASR 转写共用 `IntentClassifier` 任务分发。环境查询固定所选来源与帧序号，将同一张图的 YOLO 结果和 VLM 场景描述交给回复组织器；VLM 仅补充语义，不替代意图识别或目标检测。`OCR_READ` 默认由 RapidOCR 处理，只有没有清晰文字且视觉模型已配置时才回退到图片模型。完整多模态任务保存在 `multimodal_tasks`，不保存原始图像。

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
- 后端使用前面通过 CUDA 验证的 Conda 环境；本项目不要求固定环境名称。
- Chrome 或 Edge；真实摄像头演示需要允许摄像头访问，并准备可用的中文语音和声音输出。
- 首次安装依赖、下载 CUDA 版 PyTorch、YOLO 权重或深度模型需要网络。
- 普通文本问答需要配置对应的模型服务，使用本机模型时可安装 Ollama。默认 RapidOCR 识字在本地运行，配置的视觉模型可用于回退。
- Node.js 仅用于运行前端播报测试，不是网页运行所必需。

## 首次安装与启动

以下命令均从包含 `backend`、`frontend` 和 `scripts` 的项目根目录执行。先按上方步骤定义 `$ProjectRoot`、`$CondaEnv` 和 `$CondaExe`。已有 GPU 环境可跳过创建步骤，但必须先通过 CUDA 检查。

### 1. 使用 Conda 环境并安装依赖

```powershell
# 检查你自己的 Conda 安装和环境列表
& $CondaExe env list
& $CondaExe run -n $CondaEnv python -c "import sys; print(sys.executable)"

# CUDA PyTorch 单独安装，见上面的官方安装选择器步骤。
# 这里安装 FastAPI、YOLO、OpenCV 等应用依赖，不会安装 CPU 版 PyTorch。
& $CondaExe run -n $CondaEnv python -m pip install -r backend\requirements-conda.txt

# 不覆盖已有本机配置
if (-not (Test-Path backend\.env)) {
    Copy-Item backend\.env.example backend\.env
}
```

`$CondaEnv` 是你在本机选择的环境名；用 `& $CondaExe run -n $CondaEnv python -c "import sys; print(sys.executable)"` 查看实际解释器。`requirements.txt` 保留固定版本清单，Windows Conda 环境使用 `backend/requirements-conda.txt`。复制 `.env.example` 后请检查数据库和模型配置；不要把其他电脑的绝对路径复制进来。

不想激活环境时，用 `& $CondaExe run --no-capture-output -n $CondaEnv python ...` 执行命令。这样依赖检查和服务启动都会明确使用同一个环境。

如需使用项目根目录的 `build_handoff.py` 生成 Word 文档，请在同一 `$CondaEnv` 环境中额外安装工具依赖：

```powershell
& $CondaExe run -n $CondaEnv python -m pip install -r requirements-tools.txt
```

安装包名为 `python-docx`，代码中使用 `from docx import ...` 导入。该依赖仅用于文档工具，启动后端无需安装。

### 2. 准备 YOLO 权重

默认检查文件 `backend/models/yolov8n.pt`。如果你已把兼容的 YOLO 权重放在这个位置，可跳过下载；否则在项目根目录执行：

```powershell
New-Item -ItemType Directory -Force backend\models | Out-Null
Push-Location backend\models
try {
    & $CondaExe run -n $CondaEnv python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
} finally {
    Pop-Location
}
```

也可以在 `backend/.env` 中将 `YOLO_WEIGHTS` 设置为你自己的权重路径；相对路径以 `backend/` 为基准。权重缺失或初始化失败时，服务会报告模型不可用，不会伪造真实检测结果。

### 3. 启动后端与网页

先进入包含 `backend` 文件夹的项目根目录，再启动服务。默认使用支持 ESP32 / 平板连接的监听地址；以下命令通过变量调用你选定的 CUDA 环境。

```powershell
Set-Location -LiteralPath $ProjectRoot
& $CondaExe run --no-capture-output -n $CondaEnv python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

如果出现 `ModuleNotFoundError: No module named 'backend'`，请运行 `Get-Location` 确认当前目录，并执行 `Test-Path .\backend\main.py`，结果应为 `True`。

启动后在电脑浏览器打开 <http://127.0.0.1:8765/>。监听 `0.0.0.0` 时，本机网页和局域网设备可同时访问。仅做电脑独立演示、不接 ESP32 或局域网平板时，才使用以下仅限本机的启动命令：

```powershell
& $CondaExe run --no-capture-output -n $CondaEnv python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

设备连接地址填写电脑当前的局域网 IP，可用 `ipconfig` 查看；更换 Wi-Fi 或热点后需重新确认 IP。`0.0.0.0` 仅用于监听，不要作为浏览器或 App 的连接地址。

也可以使用启动脚本。必须先把 `QIMING_ENV` 指向已经通过 CUDA 检查的 `$CondaEnv`，脚本不会在 CUDA 失败时改用其他环境：

```powershell
$env:QIMING_ENV = $CondaEnv
$env:QIMING_CONDA = $CondaExe
$env:QIMING_ALLOW_CPU = '0'
.\scripts\start.bat
```

检查当前解释器及包依赖一致性。

```powershell
& $CondaExe run -n $CondaEnv python -c "import sys, torch; print(sys.executable); print(sys.version); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA 不可用')"
& $CondaExe run -n $CondaEnv python -m pip check
```

运行环境检查脚本时，同样设置 `$env:QIMING_ENV = $CondaEnv`、`$env:QIMING_CONDA = $CondaExe` 和 `$env:QIMING_ALLOW_CPU = '0'`。脚本会验证 CUDA 和项目依赖；它不再固定使用某个电脑上的环境名。

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

网页通过所选摄像头的检测结果使用电脑中文语音播报。帽端回复、视觉预警及家属留言通过独立 `/ws/tts` 下行：优先使用本地 WAV 缓存，未命中时异步 Edge TTS 合成并缓存为 16 kHz 单声道 PCM，ESP32 通过 MAX98357A I2S 播放并回传播放状态。紧急预警优先级最高；摄像头、马达控制和 TTS 使用独立任务/通道。真机播放需在本地配置 I2S GPIO 并烧录新固件，当前仓库尚未记录 GPIO，尚未进行本次真机试听。

网页会记住所选来源，刷新设备列表也会保留电脑摄像头的选中项。帽端离线、超时或图像过期时会清除旧画面并提示等待，不会自动调用电脑摄像头；检测结果、物品播报和识字均跟随所选来源。首次更新此功能后，请重启后端并在网页按 `Ctrl+F5` 强制刷新，无需重新烧录帽端或打包平板 App。

### 5. 拍照识字并通过电脑朗读

此功能默认使用 RapidOCR，前面通过 CUDA 验证的 `$CondaEnv` 环境已安装 RapidOCR 和 ONNX Runtime。若启用兼容视觉模型回退，还需要下方的 Ollama 配置。

1. 确认已安装 `backend/requirements-conda.txt` 中的 OCR 依赖。需要视觉模型回退时，启动 Ollama 并准备 `qwen3-vl:4b`，核对 `backend/.env` 中的 `OPENAI_BASE_URL`、`OPENAI_MODEL` 等配置。
2. 更新代码后刷新网页并登录，在“实时环境监测”顶部控制区选择并启动画面来源，等待画面出现。
3. 对准书本或标牌，在同一顶部控制区点击“识字并朗读”。每次点击只提交一张照片，不持续识字；识别结果显示在画面下方。
4. 页面显示识别原文，并通过电脑扬声器分段朗读。浏览器如阻止自动朗读，点击“重新朗读”。
5. 点击“停止识字 / 朗读”可取消当前等待或语音。停止摄像头、切换摄像头、退出登录同样会取消。取消等待后，后端已开始的 OCR 或模型任务可能仍需处理到完成或超时，短时间内再次提交可能提示忙碌。

识字及朗读期间会暂停普通物品名称播报，YOLO 检测仍继续运行；结束后恢复普通播报。系统参数中的语音总开关同时控制文字朗读，摄像头旁的“识别播报”只控制物品播报。

拍照最长宽度为 1600 像素，接口支持不超过 6 MB、1200 万像素的 JPEG / PNG。首次模型加载可能较慢；长页面建议分区域拍摄。识别结果可能漏字、错字，应与原文核对；输出因长度限制截断时会提示，并暂停自动朗读。

电脑网页继续使用浏览器语音合成；帽端使用 Edge TTS 生成 PCM 音频，经独立 `/ws/tts` 下发到 MAX98357A I2S 播放。旧固件 LU6288 UART 分支已关闭。`backend/ocr.py` 的识字服务独立于摄像头和音频输出，网页识字接口需要登录 Bearer 令牌。

## 系统总览

首页上方显示设备连接、今日预警、今日交互和中文检测模式。下方按左宽右窄排列预警趋势、模块运行状态、最近预警和最近交互；摄像头及识字操作继续集中在“实时环境监测”。

- 今日计数和近 7 天趋势从 SQLite 全量记录汇总，不受记录列表默认 100 条限制；按浏览器时区统计，接口默认 `Asia/Shanghai`。
- 趋势区分实际采集与模拟设备。设备 ID 以 `simulator_` 开头的预警标为模拟来源；交互记录中的模拟 ASR / TTS 状态也会标为软件联调。
- 模块状态分别显示后端、YOLO、对话 / 识字模型、本地摄像头和帽子设备。帽子连接以当前设备会话为准，不依赖数据库中可能过期的在线标记。
- 对话 / 识字模型通过配置接口的 `/models` 列表检查，显示“模型已安装”表示接口可达且列表包含所配置模型，不等于已完成一次实际推理。检查结果缓存 20 秒，不会触发模型加载。
- 最近预警与交互各显示最新 5 条，点击“查看全部”进入对应模块；没有数据时显示空状态。
- 总览每 10 秒自动刷新；连接失败会明确提示，已有数据保留并标为上次更新数据。首次加载失败时不会用零值替代未知统计。

新增总览接口和静态资源后需要重启后端，再刷新网页。

## 启动本地 Ollama（普通问答与视觉模型回退时需要）

YOLO 检测和默认 RapidOCR 识字在后端运行。普通问答及启用的视觉模型回退需要另行连接
模型服务。使用本机 Ollama 时，先打开 Ollama 应用，或在另一个终端执行以下命令并保持运行。
已有 Ollama 服务时可直接检查模型列表。

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

~~~powershell
& $CondaExe run -n $CondaEnv python tools\simulator.py
~~~

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
| `QIMING_VISION_CONFIG` | 空间视觉配置；相对路径以项目根目录为基准，默认 `config/v2.yaml` |
| `QIMING_DEPTH_ENABLED` | 是否启用深度模型，默认 `true` |
| `DEPTH_MODEL_PATH` | Depth Anything V2 权重路径；相对路径以项目根目录为基准 |
| `DEPTH_CODE_PATH` | Depth Anything V2 官方代码目录；相对路径以项目根目录为基准 |
| `DEPTH_INPUT_SIZE` | 深度模型输入尺寸，必须为 14 的倍数；显存不足时可谨慎下调，最低 196 |
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
- “启用障碍物预警”会控制 L1/L2/L3 风险事件和振动调度；“启用语音交互”会控制帽端风险 TTS；页面上的“识别播报”还会单独控制电脑浏览器播报。预警冷却时间会应用到同一 Track ID 的重复风险事件，最低按 2 秒执行。

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

当前仅调试摄像头，不需要连接马达。后端启动后，可在 `$CondaEnv` 环境运行
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

- 后端自动预警通过 `/ws/camera` 发送方向/马达指令，通过 `/ws/tts` 发送独立 PCM 音频；MAX98357A I2S 固件已实现，I2S GPIO 和当前固件仍需实机核验。
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
| POST | `/api/voice-vision/tasks` | 登录后提交文本、ASR 转写或图片，返回意图、同帧识别、VLM 场景描述、组合回复和阶段状态 |
| GET | `/api/voice-vision/capabilities`、`/api/voice-vision/tasks` | 登录后查看能力状态和可筛选任务历史 |
| POST / PATCH | `/api/voice-vision/tasks/{id}/speak`、`/tts` | 发起实机帽端朗读，或更新浏览器/联调朗读状态；未缓存的回复由 Edge TTS 合成，设备完成状态只由 TTS 回执更新 |
| POST | `/api/ocr/read` | 兼容接口：登录后上传 JPEG / PNG 原始图片，返回 `text`、`has_text`、`truncated` 和模型信息 |
| GET | `/api/devices` | 设备记录 |
| GET | `/api/obstacles`、`/api/voices` | 预警和交互记录 |
| GET / PUT | `/api/config` | 读取和保存配置 |
| GET | `/api/export/obstacles.xlsx` | 导出障碍物记录 |
| POST | `/api/simulate/voice` | 输入文本测试环境查询或普通问答 |
| WebSocket | `/ws/web` | 网页结果推送与控制消息 |
| WebSocket | `/ws/device` | 设备上线、心跳和二进制图像上传 |
| WebSocket | `/ws/camera` | ESP32 原始 QVGA 灰度图兼容入口；新固件加 `protocol=2` 启用 ACK 和 B/S 指令 |
| WebSocket | `/ws/audio` | 16kHz/16bit/Mono PCM 会话；`audio_start`、二进制 PCM、`audio_end` |
| WebSocket | `/ws/tts` | 按帽端设备编号连接独立 TTS 播放通道并接收播放回执 |
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

~~~powershell
& $CondaExe run -n $CondaEnv python -m pip install pytest
& $CondaExe run -n $CondaEnv python -m pytest tests -q
~~~

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
| `No module named ...` | 运行 `& $CondaExe run -n $CondaEnv python -c "import sys; print(sys.executable)"`，确认使用前面通过 CUDA 检查的环境；再用该环境执行 `-m pip install -r backend/requirements-conda.txt` 补齐依赖 |
| 启动脚本提示 CUDA 不可用 | 先运行 `nvidia-smi`，再用同一个 `$CondaEnv` 检查 `torch.cuda.is_available()`。如果驱动正常但返回 `False`，按 PyTorch 官方选择器在该环境重装匹配的 CUDA wheel；脚本会停止，不会换到 CPU 环境 |
| API 显示 `gpu.available=false`，但另一个终端 CUDA 检查为真 | 检查监听 8765 的 PID 和命令行，停止旧服务后用 `$CondaEnv` 重新启动；浏览器要访问同一个服务 |
| GPU 可用但 `depth.device=cpu` | 查看后端日志中的 CUDA OOM，关闭占用显存的程序并重启；必要时在 `backend/.env` 将 `DEPTH_INPUT_SIZE` 调小为 14 的倍数。深度模型显存不足时会临时降级 CPU，因此 YOLO GPU 可用不代表深度此刻也在 GPU 上 |
| YOLO / 深度 FPS 偏低 | 先核对 `gpu.available`、`gpu.name`、`depth.device` 和 `nvidia-smi`。FPS 是真实计时结果，帧分辨率、模型尺寸、显存压力和共享 GPU 负载都会影响它；FPS 低本身不能判断 GPU 未启用 |
| 模型显示 `unavailable` | 查看 `/api/health` 的 `model_error`，确认权重路径和依赖；修复后重启 |
| 页面能打开但无法启动摄像头 | 优先通过本机 `http://127.0.0.1:8765/` 访问，检查浏览器权限、系统隐私设置及摄像头占用；局域网 HTTP 访问可能受浏览器安全上下文限制 |
| 能看到画面但没有识别结果 | 检查后端输出、模型状态、摄像头帧上传与网页 WebSocket 连接 |
| ESP32 一直显示“等待画面” | 查看 `/api/health` 的 `devices` 是否包含 `esp32s3_cam_001`；若为空，先检查帽端供电、同一网络及后端监听地址。使用 `scripts\start.bat` 启动，固定监听 `0.0.0.0:8765`；仅监听 `127.0.0.1` 时帽端无法接入。设备已连接但仍无画面时，检查串口发送 / ACK 和图像是否已过期 |
| 有检测结果但没有声音 | 点击“测试播报”，检查“识别播报”、系统音量、中文语音和浏览器声音权限 |
| 环境查询提示无法获取可靠信息 | 先启动摄像头并等待检测，缓存默认只保留 15 秒有效期 |
| 普通问答返回降级提示 | 检查 Ollama 是否运行、模型是否下载、接口配置是否正确，以及后端系统日志 |
| 识字失败或超时 | 默认 RapidOCR 模式先检查 OCR 依赖和后端日志。使用视觉模型回退时，检查 `http://127.0.0.1:11434/api/tags`、`OPENAI_MODEL` 和服务连接，必要时启动 Ollama。保持画面清晰、减少单次文字量，首次加载稍候再试 |
| 识字完成但没有声音 | 检查系统语音总开关，点击“重新朗读”，确认浏览器声音权限与中文语音可用 |
| 更改配置后没有变化 | 重启后端；监听端口需修改启动命令，部分检测参数还会受数据库保存值影响 |
| `[Errno 10048]` 或端口 8765 被占用 | 先访问 `/api/health`，返回 `ok=true` 时直接打开登录页。需要重启时，按上方步骤核对并停止原服务，再重新启动 |

## 使用边界与后续工作

- 本项目当前适合本地或可信局域网演示。管理页面有登录检查，但业务 API 和 WebSocket 的鉴权尚未完整覆盖，CORS 也较宽松，不应直接暴露到公网。
- 目前所有设备共用容量为 1 的图像队列，优先处理最新帧；多设备隔离、慢连接处理和完整协议校验仍需完善。
- YOLO 在电脑后端运行；ESP32 负责采集、传输和执行指令，不运行 AI 推理。浏览器本地摄像头播报由浏览器完成。
- 普通目标检测不等于距离测量，检测不到物体也不代表道路安全，系统不能替代盲杖、导盲犬或专业出行辅助。
- 后续重点：填写 MAX98357A 的已核实 I2S GPIO、编译/烧录并进行帽端实音验收；接入麦克风上行 ASR，完善接口鉴权、设备重连和持续稳定性验证。

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
