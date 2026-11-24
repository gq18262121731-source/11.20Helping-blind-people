"""Monitor a real running camera session for memory, latency and frame stalls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from datetime import datetime, timezone

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--duration-minutes", type=float, default=30)
    parser.add_argument("--require-depth", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "docs" / "acceptance_artifacts" / "v2_stability.json")
    args = parser.parse_args()
    if args.duration_minutes <= 0 or args.interval_seconds <= 0:
        parser.error("duration and interval must be positive")

    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    end = started + args.duration_minutes * 60
    observations = []
    errors = []
    previous_frames = previous_depth = 0
    last_progress_at = started
    with httpx.Client(timeout=5) as client:
        while time.monotonic() < end:
            try:
                health = client.get(args.base_url.rstrip("/") + "/api/health").raise_for_status().json()
                status = client.get(args.base_url.rstrip("/") + "/api/spatial/status").raise_for_status().json()
                performance = status["performance"]
                yolo_frames = int(performance.get("yolo_frames", 0))
                depth_frames = int(performance.get("depth_frames", 0))
                if yolo_frames > previous_frames or depth_frames > previous_depth:
                    last_progress_at = time.monotonic()
                previous_frames, previous_depth = yolo_frames, depth_frames
                observations.append({
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "model_status": health.get("model_status"),
                    "depth_status": status["depth"]["status"],
                    "performance": performance,
                    "online_devices": health.get("devices", []),
                })
                if time.monotonic() - last_progress_at > max(15, args.interval_seconds * 5):
                    errors.append("camera inference made no progress for 15 seconds")
            except Exception as error:
                errors.append(f"status request failed: {type(error).__name__}: {error}")
            time.sleep(min(args.interval_seconds, max(0.0, end - time.monotonic())))

    elapsed = time.monotonic() - started
    final = observations[-1] if observations else {}
    active = bool(previous_frames)
    depth_ok = not args.require_depth or bool(previous_depth)
    healthy = bool(final and final.get("model_status") in ("ready", "simulation"))
    passed = elapsed >= args.duration_minutes * 60 and active and depth_ok and healthy and not errors
    result = {
        "status": "PASS" if passed else "BLOCKED" if not active else "FAIL",
        "started_at": started_at,
        "duration_seconds": round(elapsed, 1),
        "requested_duration_seconds": round(args.duration_minutes * 60, 1),
        "camera_inference_active": active,
        "depth_inference_active": bool(previous_depth),
        "require_depth": args.require_depth,
        "observations": observations,
        "errors": list(dict.fromkeys(errors)),
        "note": "Use a connected camera and leave its monitoring session running; this tool does not create synthetic PASS results.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "observations"},
                     ensure_ascii=False, indent=2))
    return 0 if passed else 2 if not active else 1


if __name__ == "__main__":
    raise SystemExit(main())
