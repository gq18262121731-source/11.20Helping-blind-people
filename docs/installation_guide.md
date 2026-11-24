# 第二阶段安装与运行

## 后端

```powershell
cd QimingHat\backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
cd ..
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

当前后端使用 Ultralytics 官方 YOLOv8 权重 `yolov8n.pt`。首次安装时执行：

```powershell
cd QimingHat\backend
.\.venv\Scripts\Activate.ps1
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
```

将下载得到的 `yolov8n.pt` 放入 `backend/models/`，或在 `.env` 中填写绝对路径。后端在权重不存在时显示 `model_status=unavailable`，不会伪造检测结果。

## 本地语言交互模型（Ollama）

安装并启动 Ollama 后，在终端下载项目使用的模型：

```powershell
ollama pull qwen3-vl:4b
```

`backend/.env` 中的语言交互配置为：

```dotenv
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3-vl:4b
LLM_TIMEOUT_SECONDS=300
```

这里沿用项目现有的兼容接口配置字段；请求由本机 Ollama 处理。修改配置后需要重启项目。首次请求会加载模型，超时设置为 300 秒。

从 `QimingHat` 目录运行 `scripts\start.bat`，或执行 `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765`。登录网站后，在“硬件调试 → 软件联调”中输入普通对话（如“你好，请用一句话介绍你能提供的帮助”），点击“测试语音查询”检查模型回复。环境查询继续使用已有检测结果缓存，不改动障碍物检测链路。

该切换针对识别后的文本对话模型；软件联调接口中的 ASR/TTS 仍为模拟状态。

## 本地摄像头物品语音播报

登录网站后，进入“实时环境监测”，在顶部控制区保留勾选“识别播报”，点击“启动摄像头”并允许浏览器访问摄像头。识别到物品后，浏览器使用系统中文语音播报名称及方位，例如“前方有水杯，左侧有手机”。可以先点击“测试播报”确认系统音量和扬声器工作正常。拍照识字、重新朗读和停止识字的按钮也在同一顶部控制区，识别文字显示在画面下方。

播报只使用本地摄像头的实际检测结果，沿用系统置信度设置。相同物品和方位默认每 10 秒最多播报一次，多种物品合并播报，避免积压。取消勾选“识别播报”、停止摄像头或退出登录都会停止对应播报。播报状态或错误提示显示在摄像头画面下方。

如浏览器提示尚未允许声音，请直接点击“测试播报”；如没有中文声音，请检查系统中文语音和声音输出设置。此功能使用浏览器语音合成，不经过 Ollama 对话模型，也不需要麦克风权限。

帽端播报经独立 `/ws/tts` PCM 通道传输：后端优先读本地音频缓存，未命中时用 Edge TTS + FFmpeg 异步合成后缓存；ESP32 通过 MAX98357A I2S 输出并回传播放回执。部署电脑需安装 Edge TTS（`python -m pip install -r backend/requirements-tts.txt`）及 FFmpeg。ESP32 本地配置需设置 `TTS_I2S_BCLK_PIN`、`TTS_I2S_LRCLK_PIN`、`TTS_I2S_DIN_PIN` 三个已核实的接线 GPIO。未配置时固件会报告 I2S 不可用；源码/软件闭环已实现，但烧录及真机试听仍待执行。

## 无硬件软件联调

将 `backend/.env` 中的 `SIMULATION_MODE` 改为 `true`，启动后端后另开终端运行：

```powershell
cd QimingHat
python tools\simulator.py
```

网页会显示模拟设备在线、模拟摄像头帧和明确标记为 `simulation` 的检测结果。该模式只用于软件链路测试，不能作为真实识别验收。

## 固件

Arduino IDE 安装 ESP32 Arduino Core、`ArduinoJson` 和 `WebSockets` 库，打开 `firmware/firmware.ino`，先在 `config.h` 修改 Wi-Fi、后端地址和全部 GPIO，再选择带 PSRAM 的 ESP32-S3 开发板并通过 USB 烧录。串口 115200 波特率查看摄像头初始化结果。

固件中的摄像头默认映射是常见通用 DVP 配置，嘉顺电子具体批次仍应以官方原理图核对；若摄像头初始化失败，优先只修改 `config.h`，不要改业务代码。
