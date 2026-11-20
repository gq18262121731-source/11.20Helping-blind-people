import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

@dataclass
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8765"))
    device_token: str = os.getenv("DEVICE_TOKEN", "change-me")
    yolo_weights: str = os.getenv("YOLO_WEIGHTS", str(ROOT / "models" / "yolov8n.pt"))
    yolo_confidence: float = float(os.getenv("YOLO_CONFIDENCE", "0.35"))
    cache_ttl: float = float(os.getenv("DETECTION_CACHE_TTL_SECONDS", "15"))
    database_path: str = os.getenv("DATABASE_PATH", str(ROOT / "data" / "qiming_hat.db"))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    ocr_engine: str = os.getenv("OCR_ENGINE", "rapidocr")
    ocr_model_dir: str = os.getenv("OCR_MODEL_DIR", "")
    asr_provider: str = os.getenv("ASR_PROVIDER", "sherpa-onnx")
    asr_model_dir: str = os.getenv("ASR_MODEL_DIR", "")
    asr_model: str = os.getenv("ASR_MODEL", "")
    asr_tokens: str = os.getenv("ASR_TOKENS", "")
    asr_encoder: str = os.getenv("ASR_ENCODER", "")
    asr_decoder: str = os.getenv("ASR_DECODER", "")
    asr_joiner: str = os.getenv("ASR_JOINER", "")
    audio_session_timeout_seconds: float = float(os.getenv("AUDIO_SESSION_TIMEOUT_SECONDS", "8"))
    audio_max_chunk_bytes: int = int(os.getenv("AUDIO_MAX_CHUNK_BYTES", "64000"))
    wifi_geolocation_url: str = os.getenv("WIFI_GEOLOCATION_URL", "https://beacondb.net/v1/geolocate")
    simulation_mode: bool = os.getenv("SIMULATION_MODE", "false").lower() == "true"
    left_ratio: float = 0.35
    right_ratio: float = 0.65

settings = Settings()
if not Path(settings.yolo_weights).is_absolute():
    settings.yolo_weights = str(ROOT / settings.yolo_weights)
for _name in ("asr_model_dir", "asr_model", "asr_tokens", "asr_encoder", "asr_decoder", "asr_joiner"):
    _value = getattr(settings, _name)
    if _value and not Path(_value).is_absolute():
        setattr(settings, _name, str(ROOT / _value))
Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
