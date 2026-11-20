# 启明帽 Android 平板端

Flutter 客户端，连接项目现有 FastAPI / YOLO / Qwen3-VL 电脑后端。支持服务器配置、验证码登录、系统总览、家属远程协助、手机 / 平板本机摄像头与电脑网页摄像头画面选择、当前画面识字、Android TTS 朗读与停止 / 重播、实时检测、设备列表和设置。

完整工具链、USB / 局域网运行、APK 打包与故障排查见 [平板端开发说明](../docs/tablet.md)。Python 仍使用 Conda 的 `yolo-blind`，Flutter 使用独立 SDK。

当前工程按 Flutter 3.41.9 / Dart 3.11.5 配置，首次进入 `tablet_app` 后执行 `flutter pub get`。Android 构建使用项目内 Gradle 9.5.0；如果在 Windows 中文路径下构建，`android/gradle.properties` 已启用路径检查兼容项。

在 `QimingHat` 根目录：

```powershell
# 终端一：电脑后端
conda run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765

# 终端二：连接 Android 设备，检查并运行
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action check
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action run -DeviceId "你的设备ID"

# 测试、打包
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action test
powershell -ExecutionPolicy Bypass -File .\scripts\tablet.ps1 -Action build
```

App 填写电脑局域网 IP 地址，如 `http://192.168.1.10:8765`；模拟器用 `http://10.0.2.2:8765`。账号与网页端通用，没有账号时先在电脑网页登录页注册。

查看电脑摄像头：先在电脑网页“实时环境监测”中启动摄像头，再在 App 同名页面顶部的“摄像头来源”选择“电脑网页摄像头”。可暂停观看，也可对当前收到的远端画面识字并在手机 / 平板朗读。停止上传后旧画面自动清空；切换回本机来源后需手动开启本机摄像头。此功能需要同时更新并重启后端，JPEG 画面的刷新速度受上传端限制。

家属远程协助位于独立一级入口。佩戴者可在其中授权已注册的家属账号并随时撤销；家属可查看后端转发的帽端画面、Wi-Fi 粗略位置、设备心跳和最近预警，并发送文字消息。没有绑定时页面会显示空状态，不会暴露未授权摄像头。

调试包：`tablet_app/build/app/outputs/flutter-apk/app-debug.apk`。真实平板的相机、中文声音和蓝牙仍需设备联调；帽端扬声器链路尚未接通。
