"""Small dependency-free energy VAD for deterministic software tests."""
from __future__ import annotations

import audioop


class VoiceActivityDetector:
    def __init__(self, threshold: int = 350, min_speech_ms: int = 120, hangover_ms: int = 500):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.hangover_ms = hangover_ms
        self._speech_ms = 0
        self._silence_ms = 0

    def is_speech(self, pcm: bytes, sample_rate: int = 16_000) -> bool:
        if not pcm or len(pcm) % 2:
            return False
        rms = audioop.rms(pcm, 2)
        duration_ms = len(pcm) * 1000 // (sample_rate * 2)
        if rms >= self.threshold:
            self._speech_ms += duration_ms; self._silence_ms = 0
        else:
            self._silence_ms += duration_ms
            if self._silence_ms > self.hangover_ms:
                self._speech_ms = 0
        return self._speech_ms >= self.min_speech_ms

    def reset(self):
        self._speech_ms = self._silence_ms = 0
