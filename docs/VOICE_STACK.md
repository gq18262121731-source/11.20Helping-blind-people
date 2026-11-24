# 语音闭环技术栈

本项目固定使用以下组合，后续联调不再更换模型或语音框架：

- **ASR：sherpa-onnx Zipformer Large INT8**。本地 CPU 流式识别中文语音，模型文件位于 `backend/models/asr/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30/`。配置由 `ASR_ENCODER`、`ASR_DECODER`、`ASR_JOINER` 和 `ASR_TOKENS` 指向。
- **TTS：Microsoft Edge TTS**。电脑端和帽端共用 Edge TTS，电脑端先由 FFmpeg 解码为 16 kHz、16 bit、单声道 PCM WAV，再播放；帽端沿用 `/ws/tts` 的 PCM 传输协议。
- **NLU：本地规则意图识别**。语音转写后直接进入现有规则分类器，不额外调用大语言模型。
- **视觉链路：YOLO / VLM / OCR**。语音环境问题复用同一来源和帧序的检测结果，需要场景语义时再调用 VLM。

## 当前软件闭环

实时监测页先启动摄像头，再手动启动“语音对话”。默认输入是电脑麦克风：浏览器采集音频，重采样为 16 kHz 单声道 PCM，通过 `/ws/audio` 发送；后端使用 Zipformer 流式识别、规则意图分类和现有视觉任务处理器生成回复。电脑端回复通过 `/api/tts/synthesize-browser` 获取 Edge TTS 解码后的 WAV 并播放。

页面保留“帽端麦克风（硬件接入后启用）”选项。帽端输入会继续使用同一个 `/ws/audio` 协议，但必须绑定帽端设备和 `tts_target=device`，回复经 `/ws/tts` 发送到帽端。

## 硬件验收边界

软件协议和前端入口已经保留，硬件语音闭环仍需实机完成以下工作：

1. 在 ESP32-S3 固件主循环中实际调用 `beginAudioSession()`、`sampleAudioFrame()` 和 `endAudioSession()`，并验证麦克风 PCM 能到达 `/ws/audio`。
2. 在 `firmware/esp32s3_gray_camera/config.h` 配置真实 I2S 引脚并启用 `ENABLE_TTS`；当前配置仍是 `ENABLE_TTS=0`，I2S 引脚为 `-1`。
3. 编译、烧录并验证 MAX98357A 的 I2S 播放、音量和断线恢复。
4. 只有完成上述验收后，才启用页面中的帽端麦克风选项。

电脑麦克风路径可以先用于软件联调和真实语音闭环验证，但它不能替代帽端麦克风、I2S 功放和扬声器的实机验收。
