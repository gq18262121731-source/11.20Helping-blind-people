# V2.0 性能报告

后端 `/api/spatial/status` 从真实计时器累计输出 YOLO FPS、Depth FPS、风险平均耗时和帧数。代码没有预先声称达到 15–30 FPS 或 5 FPS；应在 `torchgpu` 环境中使用同一视频记录实际结果。
