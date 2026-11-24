"""Run real RapidOCR and sherpa-onnx checks, writing machine-readable evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "test_assets"
REPORT_DIR = ROOT / "docs" / "acceptance_artifacts"


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def run_ocr():
    result = {"status": "BLOCKED", "engine": "RapidOCR", "images": [], "started_at": time.time()}
    try:
        from rapidocr_onnxruntime import RapidOCR
        import cv2
        import numpy as np
    except Exception as e:
        result["reason"] = f"RapidOCR import failed: {e}"; return result
    try:
        engine = RapidOCR()
        for path in sorted((ASSETS / "images").glob("*.jpg")):
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            started = time.perf_counter(); raw, elapsed = engine(image); duration = time.perf_counter() - started
            rows = []
            for item in raw or []:
                if len(item) >= 3: rows.append({"text": str(item[1]), "confidence": float(item[2]), "box": item[0]})
            result["images"].append({"file": str(path.relative_to(ROOT)), "sha256": file_hash(path),
                "text": "\n".join(x["text"] for x in rows), "detections": rows,
                "elapsed_seconds": duration, "engine_elapsed": elapsed})
        result["status"] = "PASS" if result["images"] and any(x["detections"] for x in result["images"] if "blank" not in x["file"]) else "FAIL"
    except Exception as e:
        result["status"] = "FAIL"; result["reason"] = repr(e)
    return result


def run_asr():
    result = {"status": "BLOCKED", "engine": "sherpa-onnx SenseVoice", "audio": [], "started_at": time.time()}
    model_root = next((p for p in (ASSETS / "models").glob("sherpa-onnx-sense-voice-*") if p.is_dir()), None)
    if model_root is None:
        result["reason"] = "ASR model archive is not prepared"; return result
    wav_path = model_root / "test_wavs" / "zh.wav"
    model = model_root / "model.int8.onnx"; tokens = model_root / "tokens.txt"
    try:
        import sherpa_onnx
        import numpy as np
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model), tokens=str(tokens), use_itn=True)
    except Exception as e:
        result["reason"] = f"sherpa-onnx load failed: {e}"; return result
    try:
        with wave.open(str(wav_path), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            pcm = wav.readframes(frames)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        started = time.perf_counter(); stream = recognizer.create_stream(); stream.accept_waveform(rate, samples); recognizer.decode_stream(stream)
        elapsed = time.perf_counter() - started
        text = str(stream.result.text or "").strip()
        result["audio"].append({"file": str(wav_path.relative_to(ROOT)), "sha256": file_hash(wav_path),
            "duration_seconds": frames / rate, "sample_rate": rate, "channels": channels,
            "sample_width": width, "text": text, "expected_text": None, "cer": None,
            "elapsed_seconds": elapsed})
        result["status"] = "PASS" if text else "FAIL"
    except Exception as e:
        result["status"] = "FAIL"; result["reason"] = repr(e)
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--ocr-only", action="store_true"); parser.add_argument("--asr-only", action="store_true")
    args = parser.parse_args(); REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    if not args.asr_only: results["ocr"] = run_ocr()
    if not args.ocr_only: results["asr"] = run_asr()
    path = REPORT_DIR / "real_model_results.json"; path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
