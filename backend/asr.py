"""Optional sherpa-onnx Mandarin ASR adapter.

The adapter keeps model construction lazy so the rest of Morning-Star remains
usable when the model files are not installed.  Tests can inject a recognizer
or use the deterministic failure path without importing sherpa-onnx.
"""
from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import dataclass

import numpy as np

from .config import settings


class ASRError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRResult:
    text: str
    confidence: float | None = None
    available: bool = True


class SherpaOnnxASR:
    def __init__(self, recognizer=None):
        self._recognizer = recognizer
        self._stream = None
        self._online_recognizer = None

    @property
    def available(self):
        return self._recognizer is not None or bool(settings.asr_model and settings.asr_tokens) or bool(settings.asr_tokens and settings.asr_encoder)

    def _load(self):
        if self._recognizer is not None:
            return self._recognizer
        if not settings.asr_tokens or (not settings.asr_model and not settings.asr_encoder):
            raise ASRError("未配置 sherpa-onnx 中文模型，请设置 ASR_MODEL 和 ASR_TOKENS")
        try:
            import sherpa_onnx
            if settings.asr_model:
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=settings.asr_model, tokens=settings.asr_tokens, use_itn=True,
                )
            else:
                kwargs = dict(tokens=settings.asr_tokens, encoder=settings.asr_encoder,
                              decoder=settings.asr_decoder, joiner=settings.asr_joiner)
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(**kwargs)
            return self._recognizer
        except Exception as error:
            raise ASRError(f"sherpa-onnx 模型加载失败: {error}") from error

    async def recognize_pcm(self, pcm: bytes, sample_rate: int = 16_000) -> ASRResult:
        if not pcm or len(pcm) % 2 or sample_rate != 16_000:
            raise ASRError("仅支持 16kHz、16bit、单声道 PCM")
        return await asyncio.to_thread(self._recognize, pcm, sample_rate)

    def _recognize(self, pcm, sample_rate):
        recognizer = self._load()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        text = str(stream.result.text or "").strip()
        return ASRResult(text=text, confidence=getattr(stream.result, "confidence", None))

    async def recognize_wav(self, data: bytes) -> ASRResult:
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16_000:
                    raise ASRError("WAV 必须是 16kHz、16bit、单声道")
                pcm = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError) as error:
            raise ASRError("WAV 文件损坏或格式不支持") from error
        return await self.recognize_pcm(pcm)

    def start_stream(self):
        """Create an OnlineRecognizer stream when an online model is supplied."""
        try:
            import sherpa_onnx
            if self._online_recognizer is None:
                self._online_recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=settings.asr_tokens, encoder=settings.asr_encoder,
                    decoder=settings.asr_decoder, joiner=settings.asr_joiner,
                    sample_rate=16_000, feature_dim=80,
                )
            return self._online_recognizer.create_stream()
        except Exception as error:
            raise ASRError(f"流式 sherpa-onnx 模型不可用: {error}") from error

    def accept_streaming_pcm(self, stream, pcm: bytes):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream.accept_waveform(16_000, samples)
        recognizer = self._online_recognizer
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        return str(stream.result.text or "").strip()


asr = SherpaOnnxASR()
