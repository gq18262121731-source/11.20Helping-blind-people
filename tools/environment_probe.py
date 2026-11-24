import importlib.util, json, platform, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

names = ["fastapi", "httpx", "cv2", "PIL", "rapidocr_onnxruntime", "onnxruntime", "sherpa_onnx", "ultralytics", "pytest", "yaml", "psutil", "serial"]
out = {"python": sys.version, "platform": platform.platform(), "modules": {name: bool(importlib.util.find_spec(name)) for name in names}}
try:
    import cv2
    import numpy
    import ultralytics
    out["versions"] = {"opencv": cv2.__version__, "numpy": numpy.__version__, "ultralytics": ultralytics.__version__}
except Exception as error:
    out["vision_package_error"] = f"{type(error).__name__}: {error}"
try:
    import torch
    cuda = bool(torch.cuda.is_available())
    out["torch"] = {"version": torch.__version__, "cuda_available": cuda,
                    "cuda_runtime": torch.version.cuda,
                    "device": torch.cuda.get_device_name(0) if cuda else None,
                    "total_memory_mb": round(torch.cuda.get_device_properties(0).total_memory / 1024**2) if cuda else None,
                    "allocated_memory_mb": round(torch.cuda.memory_allocated(0) / 1024**2) if cuda else None}
except Exception as error:
    out["torch"] = {"available": False, "error": f"{type(error).__name__}: {error}"}
try:
    from backend.config import settings
    from backend.vision.model_setup import DEPTH_WEIGHTS_SHA256, _sha256
    weights = __import__("pathlib").Path(settings.depth_model_path)
    code = __import__("pathlib").Path(settings.depth_code_path) / "depth_anything_v2" / "dpt.py"
    out["models"] = {
        "yolo_weights": {"path": settings.yolo_weights, "exists": __import__("pathlib").Path(settings.yolo_weights).is_file()},
        "depth_code_exists": code.is_file(),
        "depth_weights": {"path": str(weights), "exists": weights.is_file(),
                           "sha256_valid": weights.is_file() and _sha256(weights) == DEPTH_WEIGHTS_SHA256},
    }
except Exception as error:
    out["models"] = {"probe_error": f"{type(error).__name__}: {error}"}
try:
    import onnxruntime
    out["onnx_providers"] = onnxruntime.get_available_providers()
except Exception as error:
    out["onnx_providers"] = f"ERROR: {error}"
try:
    import sherpa_onnx
    out["sherpa_version"] = getattr(sherpa_onnx, "__version__", "installed")
except Exception as error:
    out["sherpa_version"] = f"ERROR: {error}"
try:
    import rapidocr_onnxruntime
    out["rapidocr_version"] = getattr(rapidocr_onnxruntime, "__version__", "installed")
except Exception as error:
    out["rapidocr_version"] = f"ERROR: {error}"
print(json.dumps(out, ensure_ascii=False))
