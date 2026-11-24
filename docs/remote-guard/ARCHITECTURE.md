# 架构

浏览器定位和模拟定位都写入 `runtime.locations[device_id]`，通过 `/api/v1/location/report`、WebSocket 和历史表共享。地图助手只通过后端工具访问地点和路线，前端不维护第二份位置。

CameraCapture → LatestFrameHub → YOLO/VLM/MJPEG。MJPEG 只取最新帧，不创建第二个摄像头采集循环。
