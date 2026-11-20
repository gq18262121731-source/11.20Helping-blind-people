"""Geometry-only features for deciding whether a tracked object is approaching.

The features describe image space only.  They intentionally do not claim to be
physical distance or time-to-collision measurements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SpatialFeatures:
    center_x_ratio: float
    center_y_ratio: float
    bottom_y_ratio: float
    area_ratio: float
    central_overlap: float
    image_band: str
    focus_region: bool

    def as_dict(self):
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _intersection_area(left_a, top_a, right_a, bottom_a,
                       left_b, top_b, right_b, bottom_b):
    width = max(0.0, min(right_a, right_b) - max(left_a, left_b))
    height = max(0.0, min(bottom_a, bottom_b) - max(top_a, top_b))
    return width * height


def analyze_object(bbox, frame_width, frame_height, *,
                   center_left: float = 0.30,
                   center_right: float = 0.70,
                   near_bottom: float = 0.78,
                   middle_bottom: float = 0.48):
    """Return normalized image-space features for ``[x1, y1, x2, y2]``.

    ``central_overlap`` is the fraction of the detection box covered by the
    central focus rectangle.  ``focus_region`` combines that overlap with the
    lower part of the image, where an object is more useful as a close-range
    candidate during a forward-facing demonstration.
    """

    width = max(float(frame_width or 1), 1.0)
    height = max(float(frame_height or 1), 1.0)
    if not bbox or len(bbox) != 4:
        raise ValueError("bbox must contain four coordinates")
    x1, y1, x2, y2 = (float(value) for value in bbox)
    x1, x2 = sorted((_clamp(x1 / width) * width, _clamp(x2 / width) * width))
    y1, y2 = sorted((_clamp(y1 / height) * height, _clamp(y2 / height) * height))
    box_width = max(0.0, x2 - x1)
    box_height = max(0.0, y2 - y1)
    box_area = box_width * box_height
    frame_area = width * height
    center_x = (x1 + x2) / 2.0 / width
    center_y = (y1 + y2) / 2.0 / height
    bottom_y = y2 / height
    area_ratio = box_area / frame_area

    focus_left = center_left * width
    focus_right = center_right * width
    focus_top = 0.25 * height
    focus_bottom = near_bottom * height
    overlap = _intersection_area(
        x1, y1, x2, y2,
        focus_left, focus_top, focus_right, focus_bottom,
    )
    central_overlap = overlap / box_area if box_area else 0.0
    if bottom_y >= near_bottom:
        image_band = "near_candidate"
    elif bottom_y >= middle_bottom:
        image_band = "middle_candidate"
    else:
        image_band = "far_candidate"
    focus_region = central_overlap >= 0.15 or (
        center_left <= center_x <= center_right and bottom_y >= middle_bottom
    )
    return SpatialFeatures(
        center_x_ratio=round(center_x, 4),
        center_y_ratio=round(center_y, 4),
        bottom_y_ratio=round(bottom_y, 4),
        area_ratio=round(area_ratio, 6),
        central_overlap=round(central_overlap, 4),
        image_band=image_band,
        focus_region=focus_region,
    )
