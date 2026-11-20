"""Small, dependency-light vision decision helpers used by the detector runtime."""

from .alert_controller import AlertController, AlertDecision, AlertLevel
from .approach_detector import ApproachDetector, ApproachObservation
from .spatial_analyzer import SpatialFeatures, analyze_object
from .tracker import TrackerAdapter

__all__ = [
    "AlertController",
    "AlertDecision",
    "AlertLevel",
    "ApproachDetector",
    "ApproachObservation",
    "SpatialFeatures",
    "analyze_object",
    "TrackerAdapter",
]
