import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from rapidocr_onnxruntime import RapidOCR
from backend.ocr import _rapid_text

source = ROOT / "tools/skill_demo/assets/instructions.png"
engine = RapidOCR()
start = time.perf_counter()
raw, elapsed = engine(str(source))
duration = time.perf_counter() - start
data = {
    "source_image": str(source),
    "engine": "rapidocr-onnxruntime 1.4.4",
    "onnxruntime": "1.23.2 CPUExecutionProvider",
    "duration_seconds": round(duration, 3),
    "raw": raw,
    "reading_text": _rapid_text(raw),
}
out = source.parent / "ocr_raw.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(data, ensure_ascii=True, indent=2))
