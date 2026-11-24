"""Adapter around Ultralytics' built-in BoT-SORT/ByteTrack modes."""

from __future__ import annotations


class TrackerAdapter:
    """Keep tracking optional so predict-only test doubles still work.

    The production YOLO object uses ``model.track(..., persist=True)``.  If a
    local Ultralytics build cannot load BoT-SORT, the adapter tries ByteTrack
    and finally falls back to ``predict`` while preserving the old result
    format.  A source switch resets the active tracker state.
    """

    def __init__(self):
        self.mode = "botsort"
        self.source_id = None
        self.error = None
        self.tracking_disabled = False

    def reset(self, model=None, source_id=None):
        self.source_id = source_id
        self.mode = "botsort"
        self.error = None
        self.tracking_disabled = False
        if model is None:
            return
        predictor = getattr(model, "predictor", None)
        if predictor is not None and hasattr(predictor, "trackers"):
            predictor.trackers = []

    def infer(self, model, image, *, source_id, conf, imgsz=768, half=False,
              tracking_enabled=True, device=None):
        if source_id != self.source_id:
            self.reset(model, source_id)
        common = {"conf": conf, "imgsz": imgsz, "half": half, "verbose": False}
        if device is not None:
            common["device"] = device
        track = getattr(model, "track", None)
        if tracking_enabled and track is not None and not self.tracking_disabled:
            try:
                predictions = track(
                    image, persist=True, tracker=f"{self.mode}.yaml",
                    **common,
                )
                self.error = None
                return predictions, self.mode
            except Exception as error:
                self.error = str(error)
                if self.mode == "botsort":
                    try:
                        self.mode = "bytetrack"
                        predictions = track(
                            image, persist=True, tracker="bytetrack.yaml",
                            **common,
                        )
                        self.error = None
                        return predictions, self.mode
                    except Exception as fallback_error:
                        self.error = f"BoT-SORT: {error}; ByteTrack: {fallback_error}"
                        self.mode = "predict"
                        self.tracking_disabled = True
        predict = getattr(model, "predict", None)
        if predict is None:
            raise RuntimeError("YOLO model exposes neither track nor predict")
        return predict(image, **common), "predict"
