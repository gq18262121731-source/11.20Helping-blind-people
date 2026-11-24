"""Deterministic Mandarin intent matching; no LLM required."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class Intent(str, Enum):
    OCR_READ = "OCR_READ"
    OCR_AGAIN = "OCR_AGAIN"
    CAMERA_CAPTURE = "CAMERA_CAPTURE"
    STOP = "STOP"
    ENV_QUERY = "ENV_QUERY"
    SCENE_DESCRIBE = "SCENE_DESCRIBE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class IntentMatch:
    intent: Intent
    text: str
    confidence: float


class IntentClassifier:
    def classify(self, text: str) -> IntentMatch:
        text = re.sub(r"\s+", "", str(text or "")).strip()
        if not text:
            return IntentMatch(Intent.UNKNOWN, text, 0.0)
        if any(k in text for k in ("停止", "取消", "别读", "不用了", "停下")):
            return IntentMatch(Intent.STOP, text, 0.98)
        if any(k in text for k in ("再读", "重新读", "再念", "刚才的文字")):
            return IntentMatch(Intent.OCR_AGAIN, text, 0.96)
        if any(k in text for k in ("拍张照", "拍照", "拍一张", "截图")):
            return IntentMatch(Intent.CAMERA_CAPTURE, text, 0.95)
        if any(k in text for k in ("读一下", "识别文字", "读文字", "看看字", "念一下", "文字")):
            return IntentMatch(Intent.OCR_READ, text, 0.92)
        if any(k in text for k in ("描述画面", "描述当前画面", "描述一下当前画面", "描述一下画面", "讲讲画面", "说说画面", "详细描述", "场景描述", "画面解读", "画面里有什么", "画面中有什么")):
            return IntentMatch(Intent.SCENE_DESCRIBE, text, 0.91)
        if any(k in text for k in ("前面有什么", "前方有什么", "周围有什么", "看到什么", "看到了什么", "环境", "障碍物", "左边有什么", "右边有什么", "左侧有什么", "右侧有什么")) or (
            any(side in text for side in ("前面", "前方", "左边", "左侧", "右边", "右侧", "周围"))
            and any(query in text for query in ("有没有", "是什么", "有什么", "有啥"))
        ):
            return IntentMatch(Intent.ENV_QUERY, text, 0.9)
        return IntentMatch(Intent.UNKNOWN, text, 0.1)


classifier = IntentClassifier()
