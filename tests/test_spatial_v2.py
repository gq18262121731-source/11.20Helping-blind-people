import asyncio
import sqlite3
import numpy as np

from backend.vision import (AlertEvent, AlertScheduler, SpatialFusion,
                            SpatialRiskEngine, VisionConfigError, load_vision_config)


def test_spatial_fusion_crops_roi_and_classifies_relative_depth():
    depth = np.tile(np.linspace(0.1, 1.0, 100, dtype=np.float32), (80, 1))
    result = SpatialFusion().summarize(12, [45, 20, 95, 70], depth)
    assert result.frame_id == 12
    assert result.level in {"near", "mid", "far"}
    assert result.depth_median is not None
    assert result.valid_fraction == 1.0
    assert result.as_dict()["unit"] == "relative"


def test_depth_loop_broadcasts_original_and_depth_from_the_same_frame(monkeypatch):
    import base64
    import contextlib
    import time
    from types import SimpleNamespace

    import cv2

    from backend.config import settings
    from backend.services import Runtime

    monkeypatch.setattr(settings, "depth_enabled", True)
    runtime = Runtime()
    image = np.full((60, 80, 3), 120, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    raw = encoded.tobytes()
    runtime.depth_estimator = SimpleNamespace(
        model=object(), device="cpu", precision="fp32",
        predict=lambda _: np.tile(np.linspace(0.1, 1.0, 80, dtype=np.float32), (60, 1)),
    )
    runtime.detection_history[("hat", 12)] = {
        "model_status": "ready", "capture_timestamp": "2026-11-23T00:00:00+00:00",
        "objects": [{"class": "chair", "confidence": 0.9, "bbox": [10, 10, 40, 50], "direction": "left"}],
    }
    messages = []

    async def broadcast(message):
        messages.append(message)

    runtime.broadcast = broadcast

    async def run():
        await runtime.depth_queue.put(("hat", 12, raw, time.monotonic()))
        task = asyncio.create_task(runtime.depth_loop())
        try:
            await asyncio.wait_for(runtime.depth_queue.join(), timeout=3)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert len(messages) == 1
    result = messages[0]
    assert result["type"] == "spatial_update"
    assert result["image_seq"] == result["depth_frame_id"] == 12
    assert result["frame_width"] == 80
    assert result["frame_height"] == 60
    assert base64.b64decode(result["raw_frame_preview"]) == raw
    depth_preview = cv2.imdecode(np.frombuffer(base64.b64decode(result["depth_preview"]), np.uint8), cv2.IMREAD_COLOR)
    assert depth_preview.shape[:2] == image.shape[:2]
    assert result["objects"][0]["depth"]["frame_id"] == 12


def test_versioned_yaml_configuration_is_valid():
    config = load_vision_config()
    assert config["vision"]["depth"]["model"] == "depth_anything_v2_small"
    assert sum(config["risk"][key] for key in ("distance_weight", "path_weight", "motion_weight", "ttc_weight")) == 1.0


def test_versioned_configuration_rejects_invalid_or_missing_files(tmp_path):
    import pytest
    with pytest.raises(VisionConfigError, match="不存在"):
        load_vision_config(tmp_path / "missing.yaml")
    config = tmp_path / "invalid.yaml"
    config.write_text("vision: []", encoding="utf-8")
    with pytest.raises(VisionConfigError, match="无效"):
        load_vision_config(config)


def test_obstacle_log_schema_migrates_existing_databases(tmp_path, monkeypatch):
    from backend.config import settings
    from backend.database import init_db
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE obstacle_logs(id INTEGER PRIMARY KEY, device_id TEXT, detected_at TEXT, object_class TEXT, direction TEXT, confidence REAL, voice_text TEXT, created_at TEXT)")
    monkeypatch.setattr(settings, "database_path", str(path))
    init_db()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(obstacle_logs)")}
    assert {"risk_level", "risk_score", "risk_reasons", "delivery_status"} <= columns


def test_risk_engine_requires_same_tracked_object_and_fresh_depth():
    engine = SpatialRiskEngine()
    obj = {"track_id": 3, "class": "person", "confidence": 0.9,
           "depth": {"frame_id": 1, "depth_percentile": 0.9, "quality": 0.9, "level": "near"},
           "approach": {"spatial": {"center_x_ratio": 0.5, "central_overlap": 0.8, "area_ratio": 0.08}}}
    first = engine.evaluate("hat", obj, now=1.0, measurement_age_ms=20, camera_motion_quality=0.8)
    assert first["status"] == "valid"
    obj["approach"]["spatial"]["area_ratio"] = 0.16
    second = engine.evaluate("hat", obj, now=1.4, measurement_age_ms=20, camera_motion_quality=0.8)
    assert second["visual_expansion_per_second"] > 0
    stale = engine.evaluate("hat", obj, now=2.0, measurement_age_ms=501, camera_motion_quality=0.8)
    assert stale["status"] == "unknown"
    assert stale["score"] is None
    no_camera_quality = engine.evaluate("hat", obj, now=2.2, measurement_age_ms=20,
                                        camera_motion_quality=0.0)
    assert no_camera_quality["status"] == "unknown"


def test_depth_and_spatial_cues_map_to_risk_tiers():
    engine = SpatialRiskEngine()
    def target(percentile, overlap, area=0.08):
        return {"track_id": 8, "class": "chair", "confidence": 0.95,
                "depth": {"frame_id": 1, "depth_percentile": percentile,
                          "quality": 0.95, "level": "near"},
                "approach": {"spatial": {"center_x_ratio": 0.5,
                                           "central_overlap": overlap,
                                           "area_ratio": area}}}

    light = engine.evaluate("hat", target(0.8, 0.6), now=1.0,
                            measurement_age_ms=10, camera_motion_quality=1.0)
    assert light["level"] == "L1"

    engine.reset("hat")
    near = engine.evaluate("hat", target(0.99, 1.0), now=2.0,
                           measurement_age_ms=10, camera_motion_quality=1.0)
    assert near["level"] == "L2"

    engine.reset("hat")
    engine.evaluate("hat", target(1.0, 1.0, 0.04), now=4.0,
                    measurement_age_ms=10, camera_motion_quality=1.0)
    engine.evaluate("hat", target(1.0, 1.0, 0.08), now=4.1,
                    measurement_age_ms=10, camera_motion_quality=1.0)
    urgent = engine.evaluate("hat", target(1.0, 1.0, 0.4), now=4.2,
                             measurement_age_ms=10, camera_motion_quality=1.0)
    assert urgent["level"] == "L3"


def test_alert_scheduler_escalates_and_suppresses_duplicates():
    events = []

    async def handler(event):
        events.append(event.level)

    async def run():
        scheduler = AlertScheduler(handler=handler, cooldown_seconds=2)
        base = dict(source_id="hat", track_id=4, direction="front", object_class="chair",
                    message="前方有椅子，请注意", score=0.8, reasons=("tracked_box_expanding",),
                    created_at=1.0)
        assert await scheduler.submit(AlertEvent(level="L3", **base))
        assert not await scheduler.submit(AlertEvent(level="L3", **{**base, "created_at": 1.5}))
        assert await scheduler.submit(AlertEvent(**{**base, "level": "L0", "created_at": 1.6}))
        await asyncio.sleep(0)
        await scheduler.close()
    asyncio.run(run())
    assert events == ["L3", "L0"]


def test_alert_event_carries_frame_depth_and_speech_priority():
    event = AlertEvent(
        source_id="hat", track_id=4, level="L2", direction="front",
        object_class="chair", message="注意，前方有椅子，距离较近",
        score=0.61, reasons=("relative_depth_near",), created_at=1.0,
        frame_id=27, depth_frame_id=26,
        relative_depth={"level": "near", "unit": "relative"}, speech_priority=2,
    )

    payload = event.as_dict()
    assert payload["frame_id"] == 27
    assert payload["depth_frame_id"] == 26
    assert payload["relative_depth"]["level"] == "near"
    assert payload["speech_priority"] == 2
