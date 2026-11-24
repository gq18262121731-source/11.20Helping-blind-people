"""Lightweight image-space approach trend detection.

This module consumes tracked detections.  It measures only changes in the
normalized bounding-box area and focus-region occupancy; it does not estimate
physical distance or collision time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .spatial_analyzer import SpatialFeatures, analyze_object


@dataclass(frozen=True)
class ApproachObservation:
    object_index: int
    track_key: str
    track_id: int | None
    object_class: str
    direction: str
    confidence: float
    bbox: list[float]
    spatial: SpatialFeatures
    growth_rate: float
    positive_growth_frames: int
    approaching: bool
    urgent_candidate: bool

    @property
    def level_hint(self):
        if self.urgent_candidate:
            return "URGENT"
        if self.approaching:
            return "APPROACH"
        return "NORMAL"

    def as_dict(self):
        return {
            "track_id": self.track_id,
            "object_index": self.object_index,
            "growth_rate": self.growth_rate,
            "positive_growth_frames": self.positive_growth_frames,
            "approaching": self.approaching,
            "urgent_candidate": self.urgent_candidate,
            "level_hint": self.level_hint,
            "spatial": self.spatial.as_dict(),
        }


@dataclass
class _History:
    areas: deque
    growth: deque
    last_seen: int


class ApproachDetector:
    """Track area trends independently for each camera source."""

    def __init__(self, *, history_size: int = 6, min_area_ratio: float = 0.015,
                 approach_growth: float = 0.035, urgent_growth: float = 0.08,
                 approach_frames: int = 2, urgent_frames: int = 3,
                 history_ttl_frames: int = 15):
        self.history_size = history_size
        self.min_area_ratio = min_area_ratio
        self.approach_growth = approach_growth
        self.urgent_growth = urgent_growth
        self.approach_frames = approach_frames
        self.urgent_frames = urgent_frames
        self.history_ttl_frames = history_ttl_frames
        self._histories: dict[tuple[str, str], _History] = {}
        self._frame_index: dict[str, int] = {}

    def reset(self, source_id: str | None = None):
        if source_id is None:
            self._histories.clear()
            self._frame_index.clear()
            return
        self._frame_index.pop(source_id, None)
        for key in [key for key in self._histories if key[0] == source_id]:
            self._histories.pop(key, None)

    def _key(self, source_id: str, obj: dict, spatial: SpatialFeatures) -> str:
        track_id = obj.get("track_id")
        if track_id is not None:
            return f"track:{int(track_id)}"
        # Deterministic fallback for test doubles and predict-only models.
        return f"fallback:{obj.get('class', '')}:{obj.get('direction', 'front')}:{round(spatial.center_x_ratio, 1)}"

    def update(self, source_id: str, objects: list[dict], frame_width: int,
               frame_height: int) -> list[ApproachObservation]:
        frame_index = self._frame_index.get(source_id, 0) + 1
        self._frame_index[source_id] = frame_index
        observations = []
        active_keys = set()
        for object_index, obj in enumerate(objects):
            bbox = obj.get("bbox")
            try:
                spatial = analyze_object(bbox, frame_width, frame_height)
            except (TypeError, ValueError):
                continue
            key = self._key(source_id, obj, spatial)
            active_keys.add(key)
            history = self._histories.setdefault(
                (source_id, key),
                _History(deque(maxlen=self.history_size), deque(maxlen=self.history_size - 1), frame_index),
            )
            previous = history.areas[-1] if history.areas else None
            growth = 0.0 if previous is None else (spatial.area_ratio - previous) / (previous + 1e-6)
            history.areas.append(spatial.area_ratio)
            history.growth.append(growth)
            history.last_seen = frame_index
            recent_growth = list(history.growth)
            positive = sum(value >= self.approach_growth for value in recent_growth[-self.approach_frames:])
            urgent_positive = sum(value >= self.urgent_growth for value in recent_growth[-self.urgent_frames:])
            approaching = (
                spatial.focus_region
                and spatial.area_ratio >= self.min_area_ratio
                and positive >= self.approach_frames
            )
            urgent_candidate = (
                approaching
                and spatial.central_overlap >= 0.25
                and spatial.image_band == "near_candidate"
                and urgent_positive >= self.urgent_frames
            )
            observations.append(ApproachObservation(
                object_index=object_index,
                track_key=key,
                track_id=int(obj["track_id"]) if obj.get("track_id") is not None else None,
                object_class=str(obj.get("class", "unknown")),
                direction=str(obj.get("direction", "front")),
                confidence=float(obj.get("confidence", 0.0)),
                bbox=[float(value) for value in bbox],
                spatial=spatial,
                growth_rate=round(growth, 4),
                positive_growth_frames=positive,
                approaching=approaching,
                urgent_candidate=urgent_candidate,
            ))
        for key, history in list(self._histories.items()):
            if key[0] == source_id and frame_index - history.last_seen > self.history_ttl_frames:
                self._histories.pop(key, None)
        return observations
