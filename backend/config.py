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
    frontend_live_preview: bool = os.getenv("QIMING_LIVE_PREVIEW", "false").lower() == "true"
    device_token: str = os.getenv("DEVICE_TOKEN", "change-me")
    yolo_weights: str = os.getenv("YOLO_WEIGHTS", str(ROOT / "models" / "yolov8n.pt"))
    yolo_confidence: float = float(os.getenv("YOLO_CONFIDENCE", "0.35"))
    yolo_image_size: int = int(os.getenv("YOLO_IMAGE_SIZE", "768"))
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
    asr_num_threads: int = int(os.getenv("ASR_NUM_THREADS", "2"))
    audio_session_timeout_seconds: float = float(os.getenv("AUDIO_SESSION_TIMEOUT_SECONDS", "8"))
    audio_max_chunk_bytes: int = int(os.getenv("AUDIO_MAX_CHUNK_BYTES", "64000"))
    tts_voice: str = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    tts_sample_rate: int = int(os.getenv("TTS_SAMPLE_RATE", "16000"))
    tts_ffmpeg: str = os.getenv("TTS_FFMPEG", "ffmpeg")
    depth_enabled: bool = os.getenv("QIMING_DEPTH_ENABLED", "true").lower() == "true"
    depth_model_path: str = os.getenv("DEPTH_MODEL_PATH", str(ROOT.parent / "models" / "depth-anything-v2" / "depth_anything_v2_vits.pth"))
    depth_code_path: str = os.getenv("DEPTH_CODE_PATH", str(ROOT.parent / ".tools" / "depth-anything-v2"))
    depth_update_interval_seconds: float = float(os.getenv("DEPTH_UPDATE_INTERVAL_SECONDS", "1.0"))
    depth_max_age_ms: int = int(os.getenv("DEPTH_MAX_AGE_MS", "500"))
    depth_input_size: int = int(os.getenv("DEPTH_INPUT_SIZE", "518"))
    vision_config_path: str = os.getenv("QIMING_VISION_CONFIG", "")
    wifi_geolocation_url: str = os.getenv("WIFI_GEOLOCATION_URL", "https://beacondb.net/v1/geolocate")
    amap_js_key: str = os.getenv("AMAP_JS_KEY", "")
    amap_js_security_code: str = os.getenv("AMAP_JS_SECURITY_CODE", "")
    amap_web_key: str = os.getenv("AMAP_WEB_KEY", "")
    map_js_provider: str = os.getenv("MAP_JS_PROVIDER", "baidu").strip().lower()
    baidu_map_js_ak: str = os.getenv("BAIDU_MAP_JS_AK", "").strip()
    map_service_mode: str = os.getenv("MAP_SERVICE_MODE", "cocomap").strip().lower()
    cocomap_base_url: str = os.getenv("COCOMAP_BASE_URL", "https://map.culture09.xyz").strip().rstrip("/")
    cocomap_api_key: str = os.getenv("COCOMAP_API_KEY", "").strip()
    map_demo_mode: bool = os.getenv("QIMING_MAP_DEMO_MODE", "false").lower() == "true"
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
for _name in ("depth_model_path", "depth_code_path"):
    _value = Path(getattr(settings, _name))
    if not _value.is_absolute():
        _value = ROOT.parent / _value
    setattr(settings, _name, str(_value.resolve()))
if settings.vision_config_path:
    _config_path = Path(settings.vision_config_path)
    if not _config_path.is_absolute():
        _config_path = ROOT.parent / _config_path
    settings.vision_config_path = str(_config_path.resolve())
if settings.depth_update_interval_seconds <= 0:
    raise ValueError("DEPTH_UPDATE_INTERVAL_SECONDS 必须大于 0")
if not 100 <= settings.depth_max_age_ms <= 10_000:
    raise ValueError("DEPTH_MAX_AGE_MS 必须在 100 到 10000 毫秒之间")
if settings.depth_input_size < 196 or settings.depth_input_size > 1022 or settings.depth_input_size % 14:
    raise ValueError("DEPTH_INPUT_SIZE 必须是 196 至 1022 之间的 14 的倍数")
Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
