# Morning-Star 软件阶段最终自动化验收报告

生成时间：2026-11-20T01:35:11.928699+00:00

## 1. 环境信息

- 操作系统：Windows-10-10.0.26200-SP0
- Python：3.11.15 | packaged by Anaconda, Inc. | (main, Mar 11 2026, 17:12:15) [MSC v.1942 64 bit (AMD64)]
- ONNX Runtime Provider：['AzureExecutionProvider', 'CPUExecutionProvider']
- sherpa-onnx：2.0.2
- RapidOCR：installed

## 2. 素材信息

- `images/ch_en_num.jpg`：70680 bytes，SHA256 `b94c1bf68af9ceb3c550b86931d7b48d4181c26c9f5ace2492e64042f36a02ca`，来源：https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/resources/test_files/ch_en_num.jpg
- `audio/zh.wav`：178988 bytes，SHA256 `b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373`，来源：https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
- `models/model.int8.onnx`：239233841 bytes，SHA256 `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51`，来源：https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
- `models/tokens.txt`：315894 bytes，SHA256 `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc`，来源：https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
- `images/blank.jpg`：2895 bytes，SHA256 `e2f6d0973ce3af9645a89b69c2769038b80d1345a8da761ccd8d7a6869f6584b`，来源：generated from ch_en_num.jpg
- `images/blurred.jpg`：34696 bytes，SHA256 `33baeef79e0277876abd760dc84d9054740fe37ecf77c684df5ccd34caa088d0`，来源：generated from ch_en_num.jpg
- `images/rotated.jpg`：62173 bytes，SHA256 `9f8c9a5c91abe208e9dda456fdcfb57fadf646c5d61be631af0a2f31db5f154d`，来源：generated from ch_en_num.jpg

## 3. 真实 RapidOCR 测试

结论：**PASS**
- `test_assets\images\blank.jpg`：0 个检测框，耗时 1.242s，文本长度 0
- `test_assets\images\blurred.jpg`：10 个检测框，耗时 1.978s，文本长度 54
- `test_assets\images\ch_en_num.jpg`：18 个检测框，耗时 2.091s，文本长度 100
- `test_assets\images\rotated.jpg`：18 个检测框，耗时 2.246s，文本长度 97

## 4. 真实 sherpa-onnx ASR 测试

结论：**PASS**
- `test_assets\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17\test_wavs\zh.wav`：5.592s 音频，耗时 0.426s，识别结果：`开饭时间早上9点至下午5点。`，CER：NOT_TESTED

## 5. VAD / ASR / 意图 / OCR 业务闭环

结论：**PASS**
- `帮我读一下前面的文字`：OCR_READ，状态 COMPLETED，PASS
- `再读一遍`：OCR_AGAIN，状态 COMPLETED，PASS
- `帮我拍张照`：CAMERA_CAPTURE，状态 COMPLETED，PASS
- `停止`：STOP，状态 COMPLETED，PASS
- `前面有什么`：ENV_QUERY，状态 COMPLETED，PASS
- `随便说点什么`：UNKNOWN，状态 COMPLETED，PASS
- OCR_READ：PASS，引擎 `rapidocr-onnxruntime`，真实文本长度 100

## 6. 通信协议

- 音频协议异常用例：PASS
- 覆盖：QH 帧解析、重复序列号、损坏帧、结束事件。

## 7. 完整回归

- Python pytest：PASS
- Node 前端测试：PASS

## 8. 遗留问题

- 官方 SenseVoice `zh.wav` 未提供配套人工转写文本，因此 CER 标记为 NOT_TESTED；识别本身有真实模型执行证据。
- 本次未连接 ESP32-S3，真实 ADC 噪声、Wi-Fi 丢包、摄像头传感器和马达仍属于硬件阶段。

## 9. 最终结论

软件阶段结论：**PASS**。真实 RapidOCR、真实 sherpa-onnx SenseVoice 和语音业务闭环均有独立证据。
