from backend.vision import AlertController, AlertLevel, ApproachDetector, TrackerAdapter, analyze_object


def target(box):
    return {
        "class": "person",
        "confidence": 0.9,
        "bbox": list(box),
        "direction": "front",
        "track_id": 7,
    }


def test_spatial_features_are_image_space_only():
    features = analyze_object([240, 120, 400, 420], 640, 480)
    assert 0 < features.area_ratio < 1
    assert features.focus_region is True
    assert features.image_band == "near_candidate"
    assert features.bottom_y_ratio == 0.875


def test_area_growth_becomes_approaching_then_urgent_candidate():
    detector = ApproachDetector()
    boxes = [
        [290, 170, 350, 270],
        [270, 140, 370, 320],
        [250, 110, 390, 370],
        [230, 70, 410, 430],
        [210, 40, 430, 470],
    ]
    observations = [
        detector.update("hat", [target(box)], 640, 480)[0]
        for box in boxes
    ]
    assert observations[0].approaching is False
    assert observations[2].approaching is True
    assert observations[-1].urgent_candidate is True
    assert observations[-1].growth_rate > 0


def test_alert_controller_transitions_and_clears():
    detector = ApproachDetector()
    controller = AlertController()
    boxes = [
        [290, 170, 350, 270],
        [270, 140, 370, 320],
        [250, 110, 390, 370],
        [230, 70, 410, 430],
        [210, 40, 430, 470],
        [190, 20, 450, 479],
        [170, 10, 470, 479],
    ]
    decisions = []
    for frame, box in enumerate(boxes, 1):
        observations = detector.update("hat", [target(box)], 640, 480)
        decisions.extend(controller.update("hat", observations, frame, frame))
    levels = [decision.level for decision in decisions]
    assert AlertLevel.APPROACH in levels
    assert AlertLevel.URGENT in levels
    assert decisions[-1].vibration == "both"
    assert decisions[-1].voice_text

    for frame in range(8, 12):
        decisions.extend(controller.update("hat", [], frame, frame))
    assert decisions[-1].level == AlertLevel.NORMAL
    assert decisions[-1].vibration == "stop"


def test_tracker_falls_back_once_after_botsort_and_bytetrack_fail():
    class FailingTrackerModel:
        def __init__(self):
            self.calls = []

        def track(self, image, **kwargs):
            self.calls.append(("track", kwargs["tracker"]))
            raise RuntimeError("tracker unavailable")

        def predict(self, image, **kwargs):
            self.calls.append(("predict", None))
            return ["prediction"]

    model = FailingTrackerModel()
    adapter = TrackerAdapter()
    assert adapter.infer(model, object(), source_id="hat", conf=0.35)[0] == ["prediction"]
    assert adapter.mode == "predict"
    assert adapter.tracking_disabled is True
    assert adapter.infer(model, object(), source_id="hat", conf=0.35)[0] == ["prediction"]
    assert model.calls == [
        ("track", "botsort.yaml"),
        ("track", "bytetrack.yaml"),
        ("predict", None),
        ("predict", None),
    ]


def test_tracker_passes_explicit_gpu_device_to_tracking_and_predict_fallback():
    class TrackerModel:
        def __init__(self):
            self.calls = []

        def track(self, image, **kwargs):
            self.calls.append(("track", kwargs["device"], kwargs["half"]))
            if kwargs["tracker"] == "botsort.yaml":
                raise RuntimeError("BoT-SORT config unavailable")
            raise RuntimeError("ByteTrack config unavailable")

        def predict(self, image, **kwargs):
            self.calls.append(("predict", kwargs["device"], kwargs["half"]))
            return ["prediction"]

    model = TrackerModel()
    adapter = TrackerAdapter()
    assert adapter.infer(model, object(), source_id="hat", conf=0.35,
                         half=True, device="cuda:0")[0] == ["prediction"]
    assert model.calls == [
        ("track", "cuda:0", True),
        ("track", "cuda:0", True),
        ("predict", "cuda:0", True),
    ]
