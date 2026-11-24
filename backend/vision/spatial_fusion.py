"""Join official Depth Anything V2 predictions to detections from one frame."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DepthRegion:
    frame_id: int
    depth_median: float | None
    depth_p10: float | None
    depth_p90: float | None
    depth_percentile: float | None
    valid_fraction: float
    quality: float
    level: str

    def as_dict(self):
        return {
            "frame_id": self.frame_id,
            "depth_median": self.depth_median,
            "depth_p10": self.depth_p10,
            "depth_p90": self.depth_p90,
            "depth_percentile": self.depth_percentile,
            "valid_fraction": round(self.valid_fraction, 4),
            "quality": round(self.quality, 4),
            "level": self.level,
            "unit": "relative",
        }


class SpatialFusion:
    """Summarize an inward-cropped detection ROI against a same-frame map.

    Depth Anything V2 outputs relative depth (higher values are closer), not
    metric distance. A percentile is computed within each map and is never
    compared numerically across frames.
    """

    def __init__(self, *, inset: float = 0.12, min_valid_fraction: float = 0.55):
        if not 0 <= inset < 0.45:
            raise ValueError("inset must be between 0 and 0.45")
        if not 0 < min_valid_fraction <= 1:
            raise ValueError("min_valid_fraction must be in (0, 1]")
        self.inset = inset
        self.min_valid_fraction = min_valid_fraction

    def summarize(self, frame_id: int, bbox, depth_map) -> DepthRegion:
        depth = np.asarray(depth_map, dtype=np.float32)
        if depth.ndim != 2 or not depth.size:
            raise ValueError("depth_map must be a non-empty 2D array")
        h, w = depth.shape
        if len(bbox) != 4:
            raise ValueError("bbox must contain [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(value) for value in bbox)
        if not all(np.isfinite((x1, y1, x2, y2))):
            raise ValueError("bbox coordinates must be finite")
        x1, x2 = sorted((max(0.0, min(float(w), x1)), max(0.0, min(float(w), x2))))
        y1, y2 = sorted((max(0.0, min(float(h), y1)), max(0.0, min(float(h), y2))))
        dx, dy = (x2 - x1) * self.inset, (y2 - y1) * self.inset
        left, right = int(np.floor(x1 + dx)), int(np.ceil(x2 - dx))
        top, bottom = int(np.floor(y1 + dy)), int(np.ceil(y2 - dy))
        if right <= left or bottom <= top:
            return DepthRegion(frame_id, None, None, None, None, 0.0, 0.0, "unknown")

        roi = depth[top:bottom, left:right]
        finite = np.isfinite(roi)
        valid = finite & (roi > 0)
        valid_fraction = float(valid.mean()) if valid.size else 0.0
        full = depth[np.isfinite(depth) & (depth > 0)]
        values = roi[valid]
        if len(values) < 4 or len(full) < 32 or valid_fraction < self.min_valid_fraction:
            return DepthRegion(frame_id, None, None, None, None, valid_fraction, 0.0, "unknown")

        p10, median, p90 = (float(v) for v in np.percentile(values, [10, 50, 90]))
        lo, hi = (float(v) for v in np.percentile(full, [2, 98]))
        spread = hi - lo
        if not np.isfinite(spread) or spread <= 1e-6:
            return DepthRegion(frame_id, median, p10, p90, None, valid_fraction, 0.0, "unknown")
        percentile = float(np.mean(full <= median))
        local_consistency = 1.0 - min(1.0, (p90 - p10) / spread)
        quality = valid_fraction * (0.6 + 0.4 * local_consistency)
        if percentile >= 0.67:
            level = "near"
        elif percentile >= 0.34:
            level = "mid"
        else:
            level = "far"
        return DepthRegion(
            frame_id, round(median, 5), round(p10, 5), round(p90, 5),
            round(percentile, 4), valid_fraction, quality, level,
        )

    def fuse(self, frame_id: int, objects: list[dict], depth_map) -> list[dict]:
        fused = []
        for obj in objects:
            copy = dict(obj)
            try:
                copy["depth"] = self.summarize(frame_id, copy["bbox"], depth_map).as_dict()
            except (KeyError, TypeError, ValueError, OverflowError):
                copy["depth"] = DepthRegion(frame_id, None, None, None, None, 0.0, 0.0, "unknown").as_dict()
            fused.append(copy)
        return fused
