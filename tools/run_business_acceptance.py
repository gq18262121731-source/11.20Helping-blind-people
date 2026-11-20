"""Exercise the audio WebSocket and the real OCR_READ business path."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class DeterministicASR:
    def __init__(self, text): self.text = text
    async def recognize_pcm(self, pcm):
        from backend.asr import ASRResult
        return ASRResult(self.text)


def run():
    result = {"status": "BLOCKED", "intent_cases": [], "ocr_read": None, "protocol": []}
    try:
        from fastapi.testclient import TestClient
        from backend import main
        from backend.audio_processor import AudioProcessor
        from backend.audio_protocol import AudioProtocolError, pack_audio_frame, unpack_audio_frame
        from backend.camera_stream import camera_frames
        from backend.intent import Intent
        from backend.voice_session import VoiceSessionManager
        from backend.intent.intent_classifier import IntentClassifier
    except Exception as error:
        result["reason"] = f"business imports unavailable: {error}"; return result
    image = ROOT / "test_assets" / "images" / "ch_en_num.jpg"
    if not image.exists():
        result["reason"] = "real OCR image is not prepared"; return result
    camera_frames.publish("acceptance_hat", 1, image.read_bytes())
    original = main.voice_sessions
    cases = [("帮我读一下前面的文字", Intent.OCR_READ), ("再读一遍", Intent.OCR_AGAIN),
             ("帮我拍张照", Intent.CAMERA_CAPTURE), ("停止", Intent.STOP),
             ("前面有什么", Intent.ENV_QUERY), ("随便说点什么", Intent.UNKNOWN)]
    try:
        with TestClient(main.app) as client:
            for text, expected in cases:
                # Keep the simulated helmet frame fresh; the production path
                # intentionally rejects frames older than one second.
                camera_frames.publish("acceptance_hat", 1, image.read_bytes())
                main.voice_sessions = VoiceSessionManager(processor=AudioProcessor(), recognizer=DeterministicASR(text),
                    intent_handler=main._voice_intent_handler)
                session_id = f"accept-{expected.value}-{int(time.time() * 1000)}"
                with client.websocket_connect("/ws/audio") as ws:
                    ws.send_json({"type": "audio_start", "device_id": "acceptance_hat", "session_id": session_id,
                                  "sample_rate": 16000, "channels": 1, "bits_per_sample": 16})
                    started = ws.receive_json()
                    ws.send_bytes(pack_audio_frame(b"\x01\x00" * 320, 1, int(time.time() * 1000)))
                    ws.send_json({"type": "audio_end", "session_id": session_id})
                    output = ws.receive_json()
                result["intent_cases"].append({"text": text, "expected": expected.value,
                    "actual": output.get("intent"), "state": output.get("state"),
                    "pass": output.get("intent") == expected.value})
                if expected is Intent.OCR_READ:
                    result["ocr_read"] = {"status": "PASS" if output.get("ocr_status") == "ok" and output.get("ocr", {}).get("model") == "rapidocr-onnxruntime" else "FAIL",
                                          "model": output.get("ocr", {}).get("model"), "text": output.get("reply", "")}
            processor = AudioProcessor()
            processor.start({"type": "audio_start", "device_id": "x", "session_id": "p"})
            processor.append("p", pack_audio_frame(b"\x01\x00" * 10, 1, 1))
            checks = []
            for bad in (pack_audio_frame(b"\x01\x00" * 10, 1, 1), b"QHbad"):
                try: processor.append("p", bad)
                except AudioProtocolError: checks.append(True)
            result["protocol"] = checks
    except Exception as error:
        result["reason"] = repr(error)
    finally:
        main.voice_sessions = original
    result["status"] = "PASS" if all(x["pass"] for x in result["intent_cases"]) and result["ocr_read"] and result["ocr_read"]["status"] == "PASS" and all(result["protocol"]) else "FAIL"
    return result


if __name__ == "__main__":
    data = run(); out = ROOT / "docs" / "acceptance_artifacts"; out.mkdir(parents=True, exist_ok=True)
    (out / "business_results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    raise SystemExit(0)
