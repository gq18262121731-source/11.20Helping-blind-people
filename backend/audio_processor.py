"""Session-oriented PCM validation and buffering."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .audio_buffer import AudioBuffer
from .audio_protocol import AudioStart, AudioProtocolError, unpack_audio_frame
from .config import settings
from .vad import VoiceActivityDetector


@dataclass
class AudioSession:
    start: AudioStart
    buffer: AudioBuffer
    vad: VoiceActivityDetector
    started_at: float
    last_received_at: float
    sequence: int = -1


class AudioProcessor:
    def __init__(self):
        self.sessions: dict[str, AudioSession] = {}

    def start(self, message: dict) -> AudioSession:
        start = AudioStart.from_message(message)
        now = time.monotonic()
        session = AudioSession(start, AudioBuffer(), VoiceActivityDetector(), now, now)
        self.sessions[start.session_id] = session
        return session

    def append(self, session_id: str, data: bytes) -> AudioSession:
        session = self.sessions.get(session_id)
        if not session:
            raise AudioProtocolError("未知音频会话")
        seq, payload = unpack_audio_frame(data, settings.audio_max_chunk_bytes)
        if seq and session.sequence >= 0 and seq <= session.sequence:
            raise AudioProtocolError("音频序列号必须递增")
        session.sequence = seq if seq else session.sequence + 1
        session.buffer.append(payload)
        session.vad.is_speech(payload, session.start.sample_rate)
        session.last_received_at = time.monotonic()
        return session

    def finish(self, session_id: str) -> tuple[AudioSession, bytes]:
        session = self.sessions.pop(session_id, None)
        if not session:
            raise AudioProtocolError("未知音频会话")
        return session, session.buffer.read()

    def cancel(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None
