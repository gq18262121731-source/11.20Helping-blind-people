# 本地项目与 GitHub `main` 对比报告

生成时间：2026-11-20（Asia/Shanghai）<br>
本地项目：`Morning-Star`　远程仓库：<https://github.com/altamoina-ux/Morning-Star><br>
对比基线：`origin/main`（提交 `1e3a18b2bd5ed1a1f54ed350d36758195eb0843e`，提交时间 2026-11-19 21:43:52 +08:00）

## 1. 对比结论

本地 `HEAD` 与远程 `origin/main` 指向同一个提交，因此本次差异全部来自本地工作区尚未提交的修改。当前共发现：

- 29 个已跟踪文件被修改：新增 717 行、删除 318 行。
- 33 个新增文件：约 2,920 行。
- 合计 62 个文件发生变化（不包含 `.gitignore` 排除的缓存、虚拟环境、模型和构建产物）。

## 2. 主要功能变化

### 2.1 后端视觉检测与预警

- 新增 `backend/vision/` 模块，将视觉处理拆分为空间特征分析、目标跟踪、接近趋势判断和告警状态控制。
- YOLO 推理优先使用 BoT-SORT，失败时回退到 ByteTrack，再回退到普通 `predict`，同时在检测结果中记录跟踪器名称和 `track_id`。
- 增加 `NORMAL`、`APPROACH`、`URGENT` 状态及滞回、冷却、恢复逻辑；预警记录包含接近原因、目标 ID 和模拟来源标识。
- 明确所有“接近”判断只基于图像空间面积与位置趋势，不宣称真实距离或碰撞时间。

涉及文件：`backend/services.py`、`backend/vision/*.py`、`tests/test_vision_alerts.py`、`docs/system_architecture.md`。

### 2.2 OCR 与语音交互闭环

- OCR 默认切换到 RapidOCR ONNX，保留兼容 OpenAI/Ollama 视觉模型的回退路径，并增加结果规范化、并发锁和错误提示。
- 新增 sherpa-onnx SenseVoice ASR 配置与 WAV/PCM 识别封装。
- 新增 VAD、PCM 环形缓冲、音频协议校验和会话状态机；支持 `audio_start`、二进制 PCM、`audio_end`、`audio_cancel`。
- 新增独立 `/ws/audio` WebSocket 与 `/api/simulate/audio` 本地 WAV 调试接口。
- 新增无需 LLM 的中文意图分类：识字、重新朗读、拍照、停止、环境查询和未知意图。
- 语音任务按设备限定最新画面，画面过期或设备离线时拒绝识字，避免读取错误设备或陈旧帧。

涉及文件：`backend/asr.py`、`backend/audio_*.py`、`backend/vad.py`、`backend/voice_session.py`、`backend/intent/*.py`、`backend/main.py`、`backend/ocr.py`、`backend/camera_stream.py`、`tests/test_audio.py`。

### 2.3 家属远程协助与安全监护

- 新增设备与家属账号绑定、权限控制、消息和访问日志三张 SQLite 表。
- 新增家属 API：绑定管理、设备概览、授权画面、文字消息及访问日志记录。
- 家属画面只读取后端短时缓存，过期返回 `410`；所有接口检查登录会话和逐设备授权。
- 文字协助消息可转发到已连接的帽端，使用 `FAMILY_MESSAGE:` 前缀。
- Flutter 平板新增“家属远程协助”页面，支持绑定设备、查看在线/摄像头/麦克风/位置状态、查看实时画面、发送消息和撤销绑定。

涉及文件：`backend/family.py`、`backend/database.py`、`backend/raw_camera.py`、`docs/family_assistance.md`、`tablet_app/lib/ui/family_page.dart`、`tablet_app/lib/ui/home_shell.dart`、`tablet_app/test/family_page_test.dart`。

### 2.4 Web 控制台与交互体验

- 重做 `frontend/index.html` 和 `frontend/overview.css`，形成总览、实时监测、告警记录、语音交互、设备与参数、软件联调六个页面频道。
- 总览增加值守结论、今日统计、近 7 天趋势、运行状态、最近预警和最近交互。
- 实时监测统一使用摄像头来源选择器，可在电脑摄像头与 ESP32 帽端之间切换；新增识字并朗读、重新朗读、停止和语音播报开关。
- 视觉状态明确区分实际采集与模拟设备，失败状态不会伪装成零数据或健康状态。
- `frontend/overview.js` 增加时区显示、趋势 SVG、状态卡、记录列表和错误保留逻辑。

涉及文件：`frontend/index.html`、`frontend/overview.css`、`frontend/overview.js`、`README.md`、`.workbench/spec.md`。

### 2.5 固件、配置与依赖

- 扩展 `.env.example` 和 `backend/config.py`，加入 OCR、ASR、音频会话、音频分块等配置。
- 更新音频管理和 WebSocket 管理头文件，补充新协议兼容处理。
- 更新 Conda/Python 依赖、Flutter 依赖及 Android/Gradle 配置。
- 更新通信协议和系统架构文档，补充视频源、跟踪、预警和音频通道说明。

涉及文件：`backend/.env.example`、`backend/config.py`、`backend/requirements*.txt`、`requirements-tools.txt`、`firmware/audio_manager.h`、`firmware/websocket_manager.h`、`tablet_app/pubspec.yaml`、`tablet_app/pubspec.lock`、`tablet_app/android/*`、`docs/communication_protocol.md`。

### 2.6 自动化验收与模拟工具

- 新增摄像头、音频、环境探测和测试素材准备工具。
- 新增软件验收、业务验收、真实 RapidOCR/ASR 模型测试和验收报告生成脚本。
- 新增软件阶段验收报告，记录真实 RapidOCR、sherpa-onnx、意图闭环、协议回归和遗留硬件验证项。

涉及文件：`tools/audio_simulator.py`、`tools/camera_simulator.py`、`tools/environment_probe.py`、`tools/prepare_test_assets.py`、`tools/run_business_acceptance.py`、`tools/run_real_model_tests.py`、`tools/run_software_acceptance.ps1`、`tools/generate_acceptance_report.py`、`docs/SOFTWARE_ACCEPTANCE_REPORT.md`。

## 3. 已修改的跟踪文件

| 分类 | 文件 |
| --- | --- |
| 项目与配置 | `.gitignore`、`README.md`、`requirements-tools.txt`、`scripts/check_environment.bat` |
| 后端 | `backend/.env.example`、`backend/camera_stream.py`、`backend/config.py`、`backend/database.py`、`backend/main.py`、`backend/ocr.py`、`backend/raw_camera.py`、`backend/requirements-conda.txt`、`backend/requirements.txt`、`backend/services.py` |
| 文档与协议 | `docs/communication_protocol.md`、`docs/system_architecture.md` |
| 固件 | `firmware/audio_manager.h`、`firmware/websocket_manager.h` |
| 前端 | `frontend/index.html`、`frontend/overview.css`、`frontend/overview.js` |
| 平板端 | `tablet_app/README.md`、`tablet_app/android/app/build.gradle.kts`、`tablet_app/android/gradle.properties`、`tablet_app/android/gradle/wrapper/gradle-wrapper.properties`、`tablet_app/lib/ui/home_shell.dart`、`tablet_app/pubspec.lock`、`tablet_app/pubspec.yaml` |
| 测试 | `tests/test_raw_camera.py` |

## 4. 新增文件清单

| 分类 | 新增内容 |
| --- | --- |
| 设计规范 | `.workbench/spec.md` |
| 音频与语音 | `backend/asr.py`、`backend/audio_buffer.py`、`backend/audio_processor.py`、`backend/audio_protocol.py`、`backend/audio_receiver.py`、`backend/vad.py`、`backend/voice_session.py` |
| 意图识别 | `backend/intent/__init__.py`、`backend/intent/intent_classifier.py`、`backend/intent/intent_executor.py`、`backend/intent/intent_registry.py` |
| 视觉预警 | `backend/vision/__init__.py`、`backend/vision/alert_controller.py`、`backend/vision/approach_detector.py`、`backend/vision/spatial_analyzer.py`、`backend/vision/tracker.py` |
| 家属协助 | `backend/family.py`、`docs/family_assistance.md`、`tablet_app/lib/ui/family_page.dart`、`tablet_app/test/family_page_test.dart` |
| 自动化测试 | `tests/test_audio.py`、`tests/test_vision_alerts.py` |
| 模拟与验收 | `tools/audio_simulator.py`、`tools/camera_simulator.py`、`tools/environment_probe.py`、`tools/generate_acceptance_report.py`、`tools/prepare_test_assets.py`、`tools/run_business_acceptance.py`、`tools/run_real_model_tests.py`、`tools/run_software_acceptance.ps1`、`docs/SOFTWARE_ACCEPTANCE_REPORT.md`、`docs/LOCAL_VS_GITHUB_COMPARISON.md` |

## 5. 验证结果

- `.venv\Scripts\python.exe -m pytest tests -q`：**91 passed**，4 个弃用警告。
- `node --test tests/*.test.cjs`：**36 passed**。
- 根目录直接运行 `pytest` 会额外收集硬件串口测试 `tools/test_lu6288.py`；当前环境未安装 `pyserial`，因此该硬件测试未纳入本次软件回归。
- 尚未连接 ESP32-S3，真实 ADC 噪声、无线丢包、摄像头传感器和马达仍需硬件阶段验收。

## 6. 提交范围

本报告与上述所有非忽略工作区改动一起提交到分支 `lxz11.20`，不包含虚拟环境、缓存、模型权重、数据库和构建产物。
