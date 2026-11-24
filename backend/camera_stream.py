"""Bounded, short-lived JPEG previews shared by existing camera transports."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
import re
import time


SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


@dataclass(frozen=True)
class CameraFrame:
    image: bytes
    seq: int
    received_at: float


class CameraFrames:
    def __init__(self, max_sources=8, max_age=3.0, clock=time.monotonic):
        self.max_sources = max_sources
        self.max_age = max_age
        self._clock = clock
        self._frames = OrderedDict()

    def _expire(self):
        now = self._clock()
        for source_id, frame in list(self._frames.items()):
            if now - frame.received_at >= self.max_age:
                self._frames.pop(source_id, None)

    def publish(self, source_id, seq, image):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            return
        if not 0 < len(image) < 2_000_000 or not image.startswith(b"\xff\xd8"):
            return
        self._expire()
        self._frames.pop(source_id, None)
        self._frames[source_id] = CameraFrame(bytes(image), seq, self._clock())
        while len(self._frames) > self.max_sources:
            self._frames.popitem(last=False)

    def get(self, source_id):
        self._expire()
        return self._frames.get(source_id)

    def remove(self, source_id):
        self._frames.pop(source_id, None)

    def sources(self, device_ids=()):
        self._expire()
        ids = dict.fromkeys(['local_camera', *device_ids, *self._frames])
        result = []
        for source_id in ids:
            if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
                continue
            frame = self._frames.get(source_id)
            if source_id == 'local_camera':
                label, kind = '电脑网页摄像头', 'web'
            elif source_id.startswith('tablet_'):
                label, kind = '手机 / 平板摄像头 ' + source_id[-6:], 'tablet'
            elif source_id.startswith('simulator_'):
                label, kind = '【模拟】' + source_id, 'simulation'
            else:
                label, kind = '帽端摄像头 ' + source_id, 'device'
            result.append({
                'source_id': source_id, 'label': label, 'kind': kind,
                'online': frame is not None,
                'seq': frame.seq if frame else None,
            })
        return result


camera_frames = CameraFrames()


def capture_latest_frame(source: str = "helmet", max_age_ms: int = 1000, *, device_id: str | None = None):
    """Return a fresh frame for a logical source without starting a new loop.

    ``helmet`` resolves to the newest non-local device frame, while callers can
    pass an explicit source id.  A stale or offline source returns ``None`` so
    OCR never silently consumes an old image.
    """
    if max_age_ms < 0:
        raise ValueError("max_age_ms must be non-negative")
    if source in ("helmet", "hat"):
        source = device_id or next((item["source_id"] for item in camera_frames.sources()
                                    if item["kind"] in ("device", "simulation") and item["online"]), "")
    frame = camera_frames.get(source) if source else None
    if frame is None:
        return None
    age_ms = (camera_frames._clock() - frame.received_at) * 1000
    if age_ms > max_age_ms:
        return None
    return frame
