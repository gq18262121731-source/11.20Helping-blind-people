# Android 平板端开发与联调

## 架构与范围

```text
Flutter 平板摄像头 → HTTP JPEG → 电脑 FastAPI
  ├─ 拍照识字 → Qwen3-VL → 文字 → Android TTS 本机朗读
  └─ 定时检测 → YOLO → /ws/tablet → 检测结果与预警

后续帽端：ESP32-S3 CAM → /ws/device → 同一后端 → 平板显示检测消息
```

源码在 `tablet_app/`，面向 Android 7.0 / API 24 及以上平板，宽屏固定侧边导航，窄屏底部导航。已接入服务器配置、验证码登录、可选记住登录、登录过期、系统总览、识字、停止 / 重播、平板低帧率检测、远端检测元数据、设备连接列表、检测置信度设置、本机语音设置和断线重连。

AI 仍在电脑运行，没有将 Python、YOLO 或 Ollama 打包进 APK。支持选择并查看电脑网页或已上传 JPEG 的远端摄像头画面。家属页支持平板录音、电脑本地中文识别和确认后的帽端合成播报，实体帽端的画面与扬声器仍需联网联调。平板识字没有写入后端语音交互日志，总览仍按原日志口径统计。

## 本机工具链

| 工具 | 路径或版本 |
| --- | --- |
| Flutter / Dart | `D:\development\flutter`，Flutter 3.50.0 / Dart 3.14.0 |
| Android SDK | `D:\development\android-sdk`，API 35 / 36、Build Tools 36.0.0、NDK 28.2.13676358 |
| Android Studio | `C:\Program Files\Android\Android Studio` |
| 构建 Java | Android Studio 自带 `jbr`，已通过 `flutter config` 指定 |
| 额外 JDK 17 | `C:\Program Files\Microsoft\jdk-17.0.21.21-hotspot` |
| 模拟器 | `QimingHatTablet`，Android 36 / Google APIs / x86_64，中型平板 |

Python 继续使用 Conda 的 `yolo-blind`；Flutter / Android SDK 是独立工具链，不装进 Python 虚拟环境。没有修改系统全局 PATH，使用下列脚本或 Flutter 完整路径即可。

其他电脑需安装匹配的 Flutter 稳定版、Android Studio、SDK 与命令行工具。Dart SDK 下限为 3.14，旧 Flutter 需要先升级。只开发 Android 时，可忽略 `flutter doctor` 中缺少 Visual Studio 的 Windows 桌面开发提示。

依赖通过 `pubspec.lock` 锁定。Flutter SDK 二进制使用 `storage.flutter-io.cn`，Pub 使用 `pub.dev`。Gradle 9.5.0 使用腾讯镜像，已配置与官方一致的 SHA-256。若需要官方地址，可将 `android/gradle/wrapper/gradle-wrapper.properties` 的下载地址改为 `https://services.gradle.org/distributions/gradle-9.5.0-bin.zip`，保留校验值。

## 1. 启动电脑后端

在 `QimingHat` 根目录的新终端执行：

```powershell
conda run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

如果 PowerShell 的 `conda` 函数异常，使用明确路径：

```powershell
& D:\Anaconda\Scripts\conda.exe run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

先在原终端按 Ctrl+C 停止旧服务，再运行新代码。旧进程不会自动获得新增的平板接口，且不应继续让 `backend\.venv` 的旧服务占用 8765。

识字沿用 `backend/.env` 的 Ollama 视觉模型配置。`ollama list` 中应有 `qwen3-vl:4b`；App 填写 FastAPI 地址，不是 Ollama 地址。

```powershell
ollama list
ipconfig
```

记录正在使用的网络 IPv4，如 `192.168.1.10`。先用平板浏览器访问 `http://192.168.1.10:8765/api/health`，确认返回 JSON。无法访问时检查同一 Wi-Fi、访客隔离、后端是否监听 `0.0.0.0` 和 Windows 防火墙专用网络入站设置，不需要关闭整个防火墙。

## 2. 运行 App

### 真实 Android 平板

开启平板开发者选项与 USB 调试，连接电脑，在平板上接受本机调试授权：

```powershell
# 在 QimingHat 根目录
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action check

# 替换为 flutter devices 输出的 Android 设备编号
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action run -DeviceId "你的设备ID"
```

第一次打开 App 填写 `http://电脑IPv4:8765`，如 `http://192.168.1.10:8765`，使用网页端账号和验证码登录。没有账号时先在电脑网页登录页注册。`0.0.0.0` 是监听地址；真实平板的 `127.0.0.1` 指向平板自己，都不能作为电脑地址。不要添加 `/api` 后缀。

勾选记住登录时，只在 Android 安全存储中保存会话 Token，不保存密码；不勾选时仅在当前进程中保持登录。更换服务器会退出旧会话，Token 不跨服务器复用。

### Android 模拟器

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action emulator

# 等待开机；编号以 flutter devices 为准
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action run -DeviceId emulator-5554 -ApiBaseUrl http://10.0.2.2:8765
```

`10.0.2.2` 是 Android Emulator 访问宿主电脑的地址。`-ApiBaseUrl` 只设置首次默认值，App 中已保存的地址优先；更换地址在 App 设置中操作。

也可直接运行：

```powershell
cd .\tablet_app
$env:FLUTTER_STORAGE_BASE_URL = 'https://storage.flutter-io.cn'
$env:PUB_HOSTED_URL = 'https://pub.dev'
& D:\development\flutter\bin\flutter.bat pub get
& D:\development\flutter\bin\flutter.bat devices
& D:\development\flutter\bin\flutter.bat run -d emulator-5554
```

## 3. 识字与平板本机播报

进入“实时环境监测”，打开摄像头并授权，保证光线、对焦和方向正确，再点击“拍照识字并朗读”。原始 JPEG 发给电脑，最长等待约 310 秒；首次模型加载可能较慢。完成后显示文字并默认调用 Android TTS 分段朗读，可停止、重播，也可在设置关闭自动朗读或调整语速。

### 选择电脑网页摄像头画面

1. 更新后端与 App 后，重启电脑后端；未启用 `--reload` 的旧进程不会加载新接口。
2. 在电脑浏览器登录网页，进入“实时环境监测”，点击“启动摄像头”，保持网页打开并持续上传画面。
3. 在手机 / 平板 App 的“实时环境监测”顶部，将“摄像头来源”改为“电脑网页摄像头”。App 会关闭本机摄像头，显示电脑网页上传的画面；检测结果和环境播报同步关注该来源。
4. 点击“当前画面识字并朗读”，可识别当前收到的远端画面。切换来源会取消尚未完成的识字接收，并清除上一来源的识字结果。
5. 需要使用手机 / 平板自己的摄像头时，选回“本机摄像头（手机 / 平板）”，再手动打开摄像头。前后镜头仍通过“切换镜头”选择。

远端预览为 JPEG 帧同步，刷新速度受上传端限制，并非全帧率音视频；电脑网页当前约每 700 ms 上传一帧。App 每次只拉取所选来源，支持暂停观看；后台或离开页面会停止拉取。画面停止上传或连接中断后会清空旧图并提示等待，不会将旧图持续作为实时画面展示。

远端画面通过带登录会话的接口读取，图片只在后端内存中短暂保存，不写入录像文件。相机来源列表和画面接口为 `GET /api/tablet/cameras`、`GET /api/tablet/cameras/{source_id}/frame`；未登录不能读取。帽端与其他平板上传 JPEG 后也可出现在来源列表中，未实际上传画面时显示等待。

取消识别会忽略迟到结果；电脑模型已开始的任务可能仍占用服务直到结束，再次请求可能暂时返回 429。遇到无文字、超时、服务不可用等情况会给出明确提示。

没有声音时，在“系统设置”中测试中文播报，并检查 Android“文字转语音”设置的语音引擎、中文语音包、媒体音量及蓝牙输出。离线播报由语音引擎决定，App 未内置语音包；识字仍依赖电脑连接。

使用摄像头、网络权限，家属录音时按需申请麦克风权限，不申请存储权限。录音以内存 WAV 上传至电脑本地识别，确认文字后才保存留言。相机临时 JPEG 读取后删除，识字服务可能把图片发给所配置的外部视觉模型，请确认实际模型服务位置。

## 4. 检测、设备和断线处理

“开始平板检测”约每秒上传一张 JPEG，使用独立 `tablet_` 来源 ID，不与网页 `local_camera` 混淆。后端仍用原有全局单帧队列，多个来源同时上传会竞争队列，建议一次联调一个摄像头。

顶部摄像头来源选择同时决定预览画面、识字输入和关注的检测结果，不再仅筛选检测元数据。`simulator_` 来源明确标记模拟。画面与检测结果独立更新；超过 15 秒的检测结果自动清空，空结果不表示周围安全。

环境播报默认关闭，手动开启后仅播报关注来源，带冷却并避开识字朗读。保留识字结果时暂缓环境提醒，点击“清除文字，恢复环境提醒”恢复。离开页面或切到后台会关闭相机、取消识字接收、停止上传和朗读，返回后手动开相机。

WebSocket 按 1 / 2 / 4 / 8 / 16 / 30 秒退避重连。上传遇到断线会暂停，连接恢复后手动重启检测。设备页每 10 秒使用当前后端设备连接状态，不将数据库中保存的外设标志当成自检通过。

## 5. 测试与 APK

```powershell
# 在 QimingHat 根目录
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action test
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action build
```

调试包输出：`tablet_app/build/app/outputs/flutter-apk/app-debug.apk`。可复制到自己的平板安装，也可以：

```powershell
& D:\development\android-sdk\platform-tools\adb.exe -s "你的设备ID" install -r .\tablet_app\build\app\outputs\flutter-apk\app-debug.apk
```

调试包采用开发签名，Android 清单为局域网调试允许 HTTP；原有网页与固件接口保留原访问方式。正式部署需要 HTTPS / WSS、发布签名及整个后端的统一鉴权审查。

自动测试覆盖 URL / Token 隔离、图像请求、登录失效、OCR 取消、迟到结果、分段朗读、停止竞态、多种屏幕导航、空状态与失败状态。模拟器不能替代真实平板的相机对焦、中文声音、蓝牙输出与网络切换验证。

本次验证记录（2026-11-17）：Flutter 静态分析通过，14 项 Flutter 测试通过；后端 29 项 Python 测试、原网页 22 项 Node.js 测试通过。调试 APK 已成功构建并在 Android 36 中型平板模拟器安装，已验证地址配置、真实验证码登录、总览、鉴权 WebSocket、虚拟摄像头 JPEG 上传和 YOLO 结果回传。虚拟摄像头无文字画面调用实际 OCR 后正确返回“未识别到文字”。未据此认定真实平板相机、声音或帽端硬件已验收。

摄像头来源功能验证（2026-11-18）：后端 34 项测试、Flutter 20 项测试、网页 22 项测试通过，Flutter 静态分析无问题。新增测试覆盖预览鉴权、JPEG 原样转发、来源隔离、过期与断网清图、暂停 / 后台停止拉取、来源切换忽略迟到帧，以及远端预览画面作为 OCR 输入。新版 APK 已构建并覆盖安装到 USB 连接的 Android 14 平板。安装后需重新登录；正常后端的旧进程尚需在原终端重启，电脑摄像头至平板的实拍联调待重启后完成。

需要复测时，可从项目根目录用 `yolo-blind` 执行 `python -m tools.tablet_smoke_server` 启动隔离数据库的本机 QA 服务（端口 18765）；该服务的 `tabletqa` / `Tabletqa123` 仅存在于自动创建的临时数据库，停止服务后删除，不会写入正常项目数据库。App 使用 `http://10.0.2.2:18765` 连接它。日常运行仍使用正常后端 8765 和自己的网页账号。

## 6. 后续硬件播报

平板识字与朗读仍由平板本机 TTS 播放。电脑语音工作台已将帽端回复接入“文字 → Edge TTS 或本地 WAV 缓存 → PCM 下行 → MAX98357A I2S → 帽端播放回执”。Android TTS 插件不会自动把声音转发给 ESP32。
