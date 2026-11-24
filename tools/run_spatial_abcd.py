"""Run comparable model/algorithm stages over one licensed local video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.vision import (ApproachDetector, DepthAnythingEstimator,
                            SpatialFusion, SpatialRiskEngine, TrackerAdapter)
from ultralytics import YOLO


def run(video, variant, model, estimator, *, limit, imgsz):
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"unable to open video: {video}")
    tracker = TrackerAdapter()
    approach = ApproachDetector()
    risk = SpatialRiskEngine()
    fusion = SpatialFusion()
    samples = []
    detected = depth_count = scored = 0
    seq = 0
    try:
        while seq < limit:
            ok, frame = capture.read()
            if not ok:
                break
            seq += 1
            started = time.perf_counter()
            track_enabled = variant in ("C", "D")
            predictions, _ = tracker.infer(
                model, frame, source_id="abcd", conf=settings.yolo_confidence,
                imgsz=imgsz, half=bool(variant in ("B", "C", "D") and estimator.device == "cuda"),
                tracking_enabled=track_enabled,
            ) if track_enabled else (model.predict(
                frame, conf=settings.yolo_confidence, imgsz=imgsz,
                half=bool(variant == "A" and estimator.device == "cuda"), verbose=False,
            ), "predict")
            boxes = []
            for box in predictions[0].boxes:
                coords = [float(value) for value in box.xyxy[0].tolist()]
                track_id = None
                if getattr(box, "id", None) is not None:
                    try:
                        track_id = int(box.id[0])
                    except (TypeError, ValueError, IndexError):
                        pass
                boxes.append({
                    "class": model.names[int(box.cls[0])],
                    "confidence": float(box.conf[0]), "bbox": coords,
                    "track_id": track_id,
                })
            detected += len(boxes)
            if variant in ("B", "C", "D"):
                depth = estimator.predict(frame)
                depth_count += 1
                boxes = fusion.fuse(seq, boxes, depth)
            if variant == "D":
                observations = approach.update("abcd", boxes, frame.shape[1], frame.shape[0])
                for observation in observations:
                    if 0 <= observation.object_index < len(boxes):
                        boxes[observation.object_index]["approach"] = observation.as_dict()
                for obj in boxes:
                    if obj.get("depth"):
                        score = risk.evaluate("abcd", obj, now=time.monotonic(),
                                              camera_motion_quality=1.0 if track_enabled else 0.0)
                        scored += score["status"] == "valid"
            samples.append(time.perf_counter() - started)
    finally:
        capture.release()
    return {
        "variant": variant, "frames": len(samples), "detections": detected,
        "depth_frames": depth_count, "valid_risk_scores": scored,
        "fps": round(len(samples) / max(sum(samples), 1e-6), 2),
        "median_latency_ms": round(statistics.median(samples) * 1000, 2) if samples else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="path to a locally licensed, labeled evaluation video")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--imgsz", type=int, default=settings.yolo_image_size)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video file not found: {args.video}")
    model = YOLO(settings.yolo_weights)
    estimator = DepthAnythingEstimator(settings.depth_code_path, settings.depth_model_path,
                                       input_size=settings.depth_input_size)
    estimator.load()
    variants = [run(args.video, variant, model, estimator, limit=args.limit, imgsz=args.imgsz)
                for variant in "ABCD"]
    report = {
        "status": "PARTIAL",
        "video": str(args.video.resolve()),
        "video_sha256": __import__("hashlib").sha256(args.video.read_bytes()).hexdigest(),
        "variants": variants,
        "accuracy": "NOT_TESTED: no ground-truth boxes or collision labels are accepted by this runner",
    }
    output = args.output or ROOT / "docs" / "acceptance_artifacts" / "spatial_abcd.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
