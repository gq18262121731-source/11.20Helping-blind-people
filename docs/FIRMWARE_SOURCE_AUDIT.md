# 现用 I2S 固件源码与烧录完整性审查

审查对象：`启明帽_现用I2S固件源码.zip`（2026-11-23 编制）及当前工作区后端。

## 结论

ZIP 内项目自有源码文件齐全，压缩包 CRC 正常，SHA256SUMS.txt 中 40 条文件记录全部匹配。未发现 ZIP 中途截断、文件内容损坏、重复路径或源码本地 include 缺文件。

**目前不能据此直接烧录并宣称完整功能已经得到保障。** 工具链尚未安装到这台电脑，ZIP 的本地配置已脱敏、后端地址仍写死，ZIP 中没有本次可烧录的应用镜像；更重要的是，ZIP 内 I2S 语音协议与当前工作区后端路由不兼容。编译记录没有提供最终生成的分区表镜像，所以不能确认固件是否与目标 model 分区布局匹配。

## 完整性检查

- ZIP 测试通过：41 个文件，0 个重复路径、0 个空文件；40 项 SHA-256 均通过。
- 固件自有源码：`esp32s3_gray_camera.ino`、`i2s_audio.h`、`config.h`、脱敏的 `config.local.h`、`partitions.csv` 均存在。
- 外部库 `WebSockets 2.7.2` 源码及许可证完整包含。
- 固件依赖的 Wi‑Fi、I2S、摄像头和 ESP-SR 头文件/库由 Arduino-ESP32 Core 3.3.12 / SDK 提供，**不属于 ZIP，也不是缺失的项目自有源码**。应在编译机安装与记录一致的 Core 和 ESP32-S3 工具链。
- `resources/esp_sr/srmodels.bin` 为 3,340,296 字节；自定义 `model` 分区为 0x370000（3,604,480 字节），空间足够，余量 264,184 字节。它是唤醒模型数据，不是应用固件。
- 前次 COM3 串口日志曾报告 8 MB PSRAM、`Camera init: ESP_OK` 和 `Wake word ready`，证明板上当时的固件/模型可启动；它不能证明本 ZIP 对应的分区表或镜像已经烧入该芯片。
- 编译记录中应用大小为 2,330,371 字节，小于默认方案报告的 3,342,336 字节，也小于自定义 `factory` 分区 0x480000（4,718,592 字节）。
- ZIP 没有编译出的应用 `.bin`、`.elf` 或生成后的 `partitions.bin`。所附 `compile.ps1` 明确只编译，不上传、不擦除、不写入 model 分区。
- 工作区的 `firmware/esp32s3_gray_camera/esp32s3_gray_camera.ino` 和 `config.h` 与 ZIP 哈希不同，且工作区没有 ZIP 中的 `i2s_audio.h` 和 `partitions.csv`。**不得把当前工作区 sketch 误当作 ZIP 中的现用 I2S 固件来编译。**

## 必须解决的烧录阻断项

### 1. I2S 麦克风/语音/TTS 协议与当前后端不兼容

ZIP 固件的 `HatMicrophone` 复用摄像头 `webSocket`，向 `/ws/camera` 发送 `voice_hello`、`audio_started` 等 JSON 和 `QHT2` 音频帧；播放回复时也等待 `TTS_BEGIN:`、`TTS_END` 和 `QHT2` PCM 数据。

当前工作区的 `backend/raw_camera.py` 中 `/ws/camera` 只接收 JPEG/灰度图像以及 `wifi_scan` JSON，不能处理上述语音协议；ASR 实现在独立 `/ws/audio`，设备 TTS 使用独立 `/ws/tts`，协议也与 ZIP 的 I2S 播放协议不同。因此：

- 摄像头图像/YOLO 链路可以单独验收；
- 不能把 ZIP 的 I2S 麦克风、唤醒词或帽端语音播报视为已与当前后端打通；
- 应先统一服务器与固件协议，二选一：为当前后端增加 ZIP 所用 I2S 协议适配，或将 I2S 固件改为当前 `/ws/audio` 和 `/ws/tts` 协议并补齐连接管理。

### 2. ZIP 尚未实现电脑自动发现

ZIP `config.local.h` 的 Wi‑Fi SSID、密码和 `BACKEND_HOST` 均是占位符；`esp32s3_gray_camera.ino` 仍调用 `webSocket.begin(BACKEND_HOST, BACKEND_PORT, CAMERA_WS_PATH)`。

此前生成的自动发现文档只是方案，尚未改动固件或后端。若按原 ZIP 编译，必须填入当前电脑 IP；更换运算电脑或 DHCP 地址变化后，连接仍会失败。必须先落实并测试 DNS-SD/mDNS（及所需回退），再重新计算源文件哈希。

### 3. 分区表必须在实际构建产物中确认

ZIP 自定义 CSV 声明 `factory` 大小 0x480000、`model` 从 0x490000 开始；编译记录使用 `PartitionScheme=default_8MB`，Arduino-ESP32 3.3.12 该菜单选项的 `upload.maximum_size` 是 3,342,336 字节，编译日志也报告了这个值。这个“Maximum”来自 board menu 属性，**单独既不能证明分区表错了，也不能证明 sketch 中的 CSV 确实生效了**。官方 `platform.txt` 允许 sketch 目录里的 `partitions.csv` 覆盖默认 CSV；但随包没有最终生成的 `partitions.csv` 或 `.partitions.bin`，因此目前没有证据确认这次应用构建实际使用了哪张表。

在任何写 Flash 前，必须从本机实际编译目录验证：

1. 编译产物中的 `partitions.csv` 与随包 CSV 完全一致；
2. 解析 `.partitions.bin` 后，`factory` / `model` 偏移和大小与上表一致；
3. 应用镜像在 `factory` 分区范围内；
4. model 镜像哈希对应随包 `srmodels.bin`，且完整写入 `0x490000` 分区。

### 4. 不可只靠“应用编译成功”确认唤醒模型已写入

ZIP 的 `compile.ps1` 不包含烧录或模型分区写入步骤。若将来要全片擦除、换板、改分区或初次烧录，必须先制作并验证 model 分区镜像；如果只是更新相同分区布局下的应用，应保留并读取现有 model 分区。不要执行 `erase_flash`，也不要在未备份和对照分区表前重写分区布局。

### 5. 特性范围要按所选构建标明

- ZIP 的构建脚本用 `-DENABLE_MOTORS=0`，即双马达代码保留但本次构建不触发马达；这是 USB 安全构建，不是完整马达验收构建。
- `ENABLE_TTS` 在配置中定义为 0，但 ZIP 的 I2S 扬声器收包/播放实现没有用该宏进行条件编译；不能单靠该值判断扬声器是否被编译。必须以连接正确的服务器协议、实际 GPIO、串口 I2S 初始化及真实播放验收为准。
- Wi‑Fi 配置文件脱敏是正确的分享行为，但需要在受控构建目录注入本机值；不得把密码打印到构建日志、ZIP、Git 或审查报告。

## 防止源码/镜像错配的烧录门禁

1. 将 ZIP 解压到独立只读备份和单独构建目录；保留 ZIP 原件及 SHA-256，不覆盖工作区现有不同版本。
2. 明确后端 I2S 协议适配完成，并将自动发现实现纳入构建源码；以新的 `SHA256SUMS.txt` 重新校验。
3. 在本机安装记录指定的 Arduino CLI 1.1.1、Arduino-ESP32 Core 3.3.12、ESP32-S3 OPI PSRAM 工具链；逐项核对构建参数。
4. 将 sketch 实际烧录的端口固件 `.bin`、分区表 `.bin`、model 镜像分别生成 SHA-256，记录实际地址和大小；不把只有源码/编译说明的 ZIP 当成现成固件包。
5. 读取并保存设备完整 8 MB Flash 备份；确认备份可读取、大小为 8,388,608 字节，记录芯片/板卡与备份哈希。
6. 在编译输出目录检查最终 partition CSV、partition binary、应用大小、所有 external library/core 版本；手动审查编译 warning 和分区表，不仅看退出码。
7. 烧录时不全片擦除；按已核验分区地址只写必须更新的分区。除非确认需要恢复模型分区，否则不写 `model`；如要写，先比较现有分区与目标模型镜像。
8. 烧录后通过 COM3/实际端口验收 PSRAM、相机 `ESP_OK`、GC0308、`Wake word ready`、Wi‑Fi、发现到的服务器地址、WebSocket 握手、JPEG 帧 ACK、后端 `devices` 和浏览器实帧；语音/TTS另按统一后的协议逐项测试。
9. 发生任何偏差时停止下一项写入，使用备份恢复；不以编译成功、启动日志或网页静态加载替代摄像头及音频链路验收。

## 本次审查边界

本次读取并解包检查 ZIP 文件清单、CRC、SHA-256、源码 include、构建记录、分区数据和当前后端协议。没有在当前机器重新编译（本机未检测到随包要求的 Arduino CLI/Core）、没有读取当前芯片 Flash、没有生成可烧录镜像，也没有执行烧录或擦除。ZIP 附带的编译记录属于另一台机器上的记录，不等于本机独立复现。
