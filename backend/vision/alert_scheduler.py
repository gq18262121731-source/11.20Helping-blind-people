"""Async alert deduplication with adapters for the existing speech/device APIs."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class AlertEvent:
    source_id: str
    track_id: int | None
    level: str
    direction: str
    object_class: str
    message: str
    score: float | None
    reasons: tuple[str, ...]
    created_at: float
    confidence: float = 0.0
    track_key: str = ""
    vibration: str = "stop"
    frame_id: int | None = None
    depth_frame_id: int | None = None
    relative_depth: dict | None = None
    speech_priority: int = 0

    def as_dict(self):
        return {
            "source_id": self.source_id, "track_id": self.track_id,
            "level": self.level, "direction": self.direction,
            "object": self.object_class, "message": self.message,
            "risk_score": self.score, "risk_reasons": list(self.reasons),
            "created_at": self.created_at, "confidence": self.confidence,
            "vibration": self.vibration,
            "frame_id": self.frame_id, "depth_frame_id": self.depth_frame_id,
            "relative_depth": self.relative_depth,
            "speech_priority": self.speech_priority,
        }


class AlertScheduler:
    def __init__(self, *, cooldown_seconds=2.0, max_queue=32,
                 handler=None, clock=time.monotonic):
        self.cooldown_seconds = cooldown_seconds
        self.handler = handler
        self.clock = clock
        self._last: dict[tuple[str, int | None], tuple[str, float]] = {}
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue)
        self._order = 0
        self._worker = None
        self.errors: list[dict[str, str]] = []

    def _key(self, event):
        return event.source_id, event.track_key or str(event.track_id or "unknown")

    def should_emit(self, event: AlertEvent) -> bool:
        now = event.created_at
        for key, (_, emitted_at) in list(self._last.items()):
            if now - emitted_at > 300:
                self._last.pop(key, None)
        last = self._last.get(self._key(event))
        if event.level == "L0":
            return last is not None and last[0] != "L0"
        rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
        if last is None or rank.get(event.level, 1) > rank.get(last[0], 0):
            return True
        if event.level != last[0]:
            return False
        if event.level == "L0":
            return False
        if now - last[1] < self.cooldown_seconds:
            return False
        return True

    async def submit(self, event: AlertEvent) -> bool:
        if not self.should_emit(event):
            return False
        priority = {"L3": 0, "L2": 1, "L1": 2, "L0": 3}.get(event.level, 2)
        try:
            self._queue.put_nowait((priority, self._order, event))
            self._order += 1
        except asyncio.QueueFull:
            return False
        self._last[self._key(event)] = (event.level, event.created_at)
        if self._worker is None or self._worker.done():
            self.start()
        return True

    def start(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self.run())

    async def run(self):
        while True:
            _priority, _order, event = await self._queue.get()
            try:
                if self.handler:
                    result = self.handler(event)
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:
                import traceback
                self.errors.append({"event": event.level, "error": traceback.format_exc(limit=1).strip()})
                del self.errors[:-50]
            finally:
                self._queue.task_done()

    async def close(self):
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None

    def clear(self, source_id: str | None = None):
        if source_id is None:
            self._last.clear()
        else:
            for key in list(self._last):
                if key[0] == source_id:
                    self._last.pop(key, None)
        pending = []
        while True:
            try:
                item = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
            if source_id is None or item[2].source_id != source_id:
                pending.append(item)
        for item in pending:
            self._queue.put_nowait(item)
