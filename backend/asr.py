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
from pathlib import Path

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
        if self._recognizer is not None:
            return True
        paths = [settings.asr_tokens, settings.asr_encoder, settings.asr_decoder, settings.asr_joiner]
        if all(paths):
            return all(Path(path).is_file() for path in paths)
        return bool(settings.asr_model and settings.asr_tokens and
                    Path(settings.asr_model).is_file() and Path(settings.asr_tokens).is_file())

    def _load(self):
        if self._recognizer is not None:
            return self._recognizer
        if not settings.asr_tokens or not settings.asr_encoder or not settings.asr_decoder or not settings.asr_joiner:
            raise ASRError("未配置 Zipformer 中文模型，请设置 ASR_ENCODER、ASR_DECODER、ASR_JOINER 和 ASR_TOKENS")
        try:
            import sherpa_onnx
            if settings.asr_model and not settings.asr_encoder:
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=settings.asr_model, tokens=settings.asr_tokens, use_itn=True,
                )
            else:
                self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=settings.asr_tokens, encoder=settings.asr_encoder,
                    decoder=settings.asr_decoder, joiner=settings.asr_joiner,
                    num_threads=settings.asr_num_threads, provider="cpu",
                    sample_rate=16_000, feature_dim=80,
                    decoding_method="greedy_search",
                    enable_endpoint_detection=True,
                )
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
        if hasattr(recognizer, "get_result"):
            stream = recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples)
            stream.input_finished()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            text = str(recognizer.get_result(stream) or "").strip()
            return ASRResult(text=text)
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
        """Create the fixed Zipformer INT8 CPU stream."""
        try:
            recognizer = self._load()
            if not hasattr(recognizer, "get_result"):
                raise ASRError("当前 ASR 不是 Zipformer 流式模型")
            self._online_recognizer = recognizer
            return recognizer.create_stream()
        except ASRError:
            raise
        except Exception as error:
            raise ASRError(f"Zipformer 流式模型不可用: {error}") from error

    def accept_streaming_pcm(self, stream, pcm: bytes):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream.accept_waveform(16_000, samples)
        recognizer = self._online_recognizer
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        return str(recognizer.get_result(stream) or "").strip()

    def finish_stream(self, stream):
        recognizer = self._online_recognizer or self._load()
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = str(recognizer.get_result(stream) or "").strip()
        return ASRResult(text=text)


asr = SherpaOnnxASR()
