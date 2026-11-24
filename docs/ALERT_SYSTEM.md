# AlertScheduler

告警按来源和 Track ID 去重。L1、L2、L3 都会生成风险语音文本和 `speech_priority`，其中 L3 最高；重复等级进入冷却窗口，L0 负责发送一次停止振动。每条 `obstacle_alert` 还带有检测帧号、深度帧号、相对深度摘要、风险原因和评分，网页可用这些字段把语音与深度对照画面对应起来。

电脑端收到 `obstacle_alert` 后使用独立的风险语音队列：L1/L2 等待当前普通播报结束并保留最高待播等级，L3 立即取消普通播报并插入危险提示。帽端 TTS 使用同样的优先级映射（L3=0、L2=1、L1=2，数值越小越优先）。调度器只调用现有 `runtime.send_device`，由 `RawCameraConnection` 按协议转换为振动字节和 TTS 队列。事件中的 `adapter_received` 表示 WebSocket 适配器已接受发送，不能证明外设马达实际振动；`requested_on_device` 表示语音请求已经交给设备链路，不能证明扬声器已播放。硬件离线时显示 `device_offline`。
