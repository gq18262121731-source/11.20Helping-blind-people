"""Conservative image-space risk cues; outputs are scores, never probabilities."""
from __future__ import annotations

from collections import deque


class SpatialRiskEngine:
    def __init__(self, *, max_depth_age_ms=500, quality_floor=0.35,
                 weights=(0.30, 0.30, 0.20, 0.20), clear_after_seconds=2.0):
        if len(weights) != 4 or any(value < 0 for value in weights) or abs(sum(weights) - 1) > 1e-6:
            raise ValueError("risk weights must be four non-negative values that sum to one")
        if max_depth_age_ms <= 0 or not 0 <= quality_floor <= 1:
            raise ValueError("invalid risk quality settings")
        self.max_depth_age_ms = max_depth_age_ms
        self.quality_floor = quality_floor
        self.weights = tuple(float(value) for value in weights)
        self.clear_after_seconds = clear_after_seconds
        self._history: dict[tuple[str, int], tuple[float, float]] = {}
        self._scores: dict[tuple[str, int], deque[float]] = {}
        self._last_cleanup = 0.0

    def reset(self, source_id: str | None = None):
        for storage in (self._history, self._scores):
            for key in list(storage):
                if source_id is None or key[0] == source_id:
                    storage.pop(key, None)

    def evaluate(self, source_id: str, obj: dict, *, now: float,
                 measurement_age_ms: float = 0.0, camera_motion_quality: float = 1.0) -> dict:
        track_id = obj.get("track_id")
        depth = obj.get("depth") or {}
        approach = obj.get("approach") or {}
        spatial = approach.get("spatial") or {}
        key = None
        if track_id is not None:
            try:
                key = (source_id, int(track_id))
            except (TypeError, ValueError):
                key = None

        if now - self._last_cleanup >= 2.0:
            expired = [item for item, (seen, _) in self._history.items()
                       if now - seen > self.clear_after_seconds]
            for item in expired:
                self._history.pop(item, None)
                self._scores.pop(item, None)
            self._last_cleanup = now

        quality = float(depth.get("quality") or 0.0)
        confidence = float(obj.get("confidence") or 0.0)
        percentile = depth.get("depth_percentile")
        center_x = float(spatial.get("center_x_ratio") or 0.0)
        overlap = float(spatial.get("central_overlap") or 0.0)
        area = float(spatial.get("area_ratio") or 0.0)
        if (
            key is None or depth.get("frame_id") is None or percentile is None
            or measurement_age_ms < 0 or measurement_age_ms > self.max_depth_age_ms
            or quality < self.quality_floor or confidence < 0.35
            or camera_motion_quality < 0.5
        ):
            return {
                "status": "unknown", "score": None, "level": "UNKNOWN",
                "unit": "internal_score", "reasons": ["depth_track_or_camera_motion_quality_insufficient"],
                "depth_age_ms": max(0, round(measurement_age_ms)),
            }

        previous = self._history.get(key)
        expansion = None
        ttc = None
        if previous:
            dt = now - previous[0]
            if 0.05 <= dt <= 1.5 and previous[1] > 1e-6:
                expansion = max(-4.0, min(4.0, (area - previous[1]) / (previous[1] * dt)))
                if expansion > 0.03:
                    ttc = 1.0 / expansion
        self._history[key] = (now, area)

        distance_risk = max(0.0, min(1.0, (float(percentile) - 0.45) / 0.55))
        path_risk = max(0.0, min(1.0, overlap if overlap else 1.0 - abs(center_x - 0.5) / 0.5))
        motion_risk = 1.0 if expansion is not None and expansion > 0.03 else 0.0
        ttc_risk = max(0.0, min(1.0, 1.0 - ttc / 3.0)) if ttc is not None else 0.0
        raw = sum(w * value for w, value in zip(self.weights,
                    (distance_risk, path_risk, motion_risk, ttc_risk)))
        score_window = self._scores.setdefault(key, deque(maxlen=3))
        score_window.append(raw)
        score = sum(score_window) / len(score_window)
        if score >= 0.75:
            level = "L3"
        elif score >= 0.55:
            level = "L2"
        elif score >= 0.35:
            level = "L1"
        else:
            level = "L0"
        reasons = []
        if distance_risk >= 0.5:
            reasons.append("relative_depth_near")
        if path_risk >= 0.5:
            reasons.append("front_focus_region")
        if motion_risk:
            reasons.append("tracked_box_expanding")
        if ttc is not None and ttc <= 3:
            reasons.append("short_visual_expansion_horizon")
        return {
            "status": "valid", "score": round(score, 4), "level": level,
            "unit": "internal_score", "reasons": reasons or ["no_high_risk_cue"],
            "depth_age_ms": max(0, round(measurement_age_ms)),
            "relative_depth_level": depth.get("level", "unknown"),
            "visual_expansion_per_second": round(expansion, 4) if expansion is not None else None,
            "auxiliary_ttc_seconds": round(ttc, 2) if ttc is not None else None,
            "score_is_probability": False,
        }
