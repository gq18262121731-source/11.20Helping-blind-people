from .intent_classifier import Intent

INTENT_REGISTRY = {
    Intent.OCR_READ: {"description": "获取最新画面并识别文字"},
    Intent.OCR_AGAIN: {"description": "重复播报上一次识别结果"},
    Intent.CAMERA_CAPTURE: {"description": "获取当前画面快照"},
    Intent.STOP: {"description": "取消正在执行的语音任务"},
    Intent.ENV_QUERY: {"description": "查询最新视觉检测结果"},
    Intent.SCENE_DESCRIBE: {"description": "调用视觉模型描述当前画面，再结合检测结果回复"},
    Intent.UNKNOWN: {"description": "提示用户重新表达"},
}
