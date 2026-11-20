"""Small dependency-injected executor for the registered intents."""
from __future__ import annotations

from .intent_classifier import Intent, IntentClassifier


class IntentExecutor:
    def __init__(self, *, classifier=None, handlers=None):
        self.classifier = classifier or IntentClassifier()
        self.handlers = handlers or {}

    async def execute(self, text: str, **context):
        match = self.classifier.classify(text)
        handler = self.handlers.get(match.intent)
        if handler is None:
            if match.intent is Intent.UNKNOWN:
                return {"intent": match.intent.value, "reply": "我没有听懂，请说再读一遍、读一下文字或前面有什么。"}
            return {"intent": match.intent.value, "reply": "当前功能尚未连接。"}
        result = handler(match, **context)
        if hasattr(result, "__await__"):
            result = await result
        return {"intent": match.intent.value, **(result or {})}
