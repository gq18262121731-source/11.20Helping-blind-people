import importlib.util, json, platform, sys

names = ["fastapi", "httpx", "cv2", "PIL", "rapidocr_onnxruntime", "onnxruntime", "sherpa_onnx", "ultralytics", "pytest"]
out = {"python": sys.version, "platform": platform.platform(), "modules": {name: bool(importlib.util.find_spec(name)) for name in names}}
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
