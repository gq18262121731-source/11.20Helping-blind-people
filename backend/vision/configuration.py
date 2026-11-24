"""Load and validate the small, versioned spatial runtime configuration."""
from __future__ import annotations

from pathlib import Path
import os


class VisionConfigError(ValueError):
    pass


def load_vision_config(path=None):
    project_root = Path(__file__).resolve().parents[2]
    path = Path(path or os.getenv("QIMING_VISION_CONFIG") or project_root / "config" / "v2.yaml")
    if not path.is_absolute():
        path = project_root / path
    try:
        import yaml
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VisionConfigError(f"空间感知配置文件不存在：{path}") from error
    except Exception as error:
        raise VisionConfigError(f"空间感知配置读取失败：{error}") from error
    if not isinstance(config, dict):
        raise VisionConfigError("空间感知配置根节点必须是对象")

    try:
        detector = config["vision"]["detector"]
        depth = config["vision"]["depth"]
        tracking = config["vision"]["tracking"]
        zones = config["spatial"]["zones"]
        risk = config["risk"]
        alerts = config["alert"]
        runtime = config["runtime"]
        imgsz = int(detector["imgsz"])
        update_fps = float(depth["update_fps"])
        input_size = int(depth["input_size"])
        max_age_ms = int(depth["max_age_ms"])
        left, right = float(zones["left"]), float(zones["right"])
        weights = tuple(float(risk[key]) for key in (
            "distance_weight", "path_weight", "motion_weight", "ttc_weight"))
        if not 64 <= imgsz <= 1536:
            raise ValueError("vision.detector.imgsz 必须在 64 到 1536 之间")
        if not 0.1 <= update_fps <= 10:
            raise ValueError("vision.depth.update_fps 必须在 0.1 到 10 之间")
        if not 196 <= input_size <= 1022 or input_size % 14:
            raise ValueError("vision.depth.input_size 必须是 196 到 1022 之间的 14 的倍数")
        if not 100 <= max_age_ms <= 10_000:
            raise ValueError("vision.depth.max_age_ms 必须在 100 到 10000 之间")
        if not 0.1 <= left < right <= 0.9:
            raise ValueError("spatial.zones 必须满足 0.1 <= left < right <= 0.9")
        if detector.get("precision") not in ("fp16", "fp32"):
            raise ValueError("vision.detector.precision 必须是 fp16 或 fp32")
        if depth.get("precision") not in ("fp16", "fp32"):
            raise ValueError("vision.depth.precision 必须是 fp16 或 fp32")
        if depth.get("model") != "depth_anything_v2_small":
            raise ValueError("当前配置只允许 Depth Anything V2 Small")
        if any(weight < 0 for weight in weights) or abs(sum(weights) - 1) > 1e-6:
            raise ValueError("risk 权重必须非负且总和为 1")
        for name, flag in (("vision.depth.enabled", depth["enabled"]),
                           ("vision.detector.enabled", detector["enabled"]),
                           ("vision.tracking.enabled", tracking["enabled"]),
                           ("alert.enabled", alerts["enabled"]),
                           ("alert.tts_enabled", alerts["tts_enabled"]),
                           ("alert.vibration_enabled", alerts["vibration_enabled"]),
                           ("runtime.latest_frame_only", runtime["latest_frame_only"])):
            if not isinstance(flag, bool):
                raise ValueError(f"{name} 必须为布尔值")
        if runtime["latest_frame_only"] is not True:
            raise ValueError("runtime.latest_frame_only 必须启用")
    except (KeyError, TypeError, ValueError) as error:
        raise VisionConfigError(f"空间感知配置无效：{error}") from error
    return config
