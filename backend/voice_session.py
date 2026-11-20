"""Half-duplex speech session state machine shared by hardware and simulators."""
from __future__ import annotations

import asyncio
from enum import Enum

from .asr import ASRError, asr
from .audio_processor import AudioProcessor
from .intent import classifier


class VoiceState(str, Enum):
    LISTENING = "LISTENING"
    RECORDING = "RECORDING"
    RECOGNIZING = "RECOGNIZING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class VoiceSessionManager:
    def __init__(self, *, processor=None, recognizer=None, intent_handler=None, timeout=8.0):
        self.processor = processor or AudioProcessor()
        self.recognizer = recognizer or asr
        self.intent_handler = intent_handler
        self.timeout = timeout
        self.active: dict[str, str] = {}
        self.state: dict[str, VoiceState] = {}
        self.streams = {}
        self.partial_text = {}

    def start(self, message):
        device_id = str(message.get("device_id", ""))
        if device_id in self.active:
            raise RuntimeError("已有语音任务正在执行")
        if str(message.get("session_id", "")) in self.processor.sessions:
            raise RuntimeError("session_id 已在使用")
        session = self.processor.start(message)
        self.active[session.start.device_id] = session.start.session_id
        self.state[session.start.session_id] = VoiceState.RECORDING
        try:
            self.streams[session.start.session_id] = self.recognizer.start_stream()
        except (AttributeError, ASRError):
            # Offline-only model adapters remain valid; recognition runs at end.
            pass
        return session

    def append(self, session_id, data):
        session = self.processor.append(session_id, data)
        stream = self.streams.get(session_id)
        if stream is not None:
            try:
                self.partial_text[session_id] = self.recognizer.accept_streaming_pcm(stream, data[26:] if data.startswith(b"QH") else data)
            except Exception:
                self.partial_text[session_id] = ""
        return session

    async def finish(self, session_id):
        session, pcm = self.processor.finish(session_id)
        self.state[session_id] = VoiceState.RECOGNIZING
        try:
            result = await asyncio.wait_for(self.recognizer.recognize_pcm(pcm), self.timeout)
            if not result.text:
                raise ASRError("没有识别到语音")
            self.state[session_id] = VoiceState.PROCESSING
            match = classifier.classify(result.text)
            output = await self.intent_handler(match, session) if self.intent_handler else {
                "intent": match.intent.value, "text": result.text
            }
            self.state[session_id] = VoiceState.COMPLETED
            return {"session_id": session_id, "device_id": session.start.device_id,
                    "text": result.text, "asr_status": "ok", **(output or {})}
        except (ASRError, asyncio.TimeoutError) as error:
            self.state[session_id] = VoiceState.LISTENING
            return {"session_id": session_id, "device_id": session.start.device_id,
                    "text": "", "asr_status": "failed", "error": str(error)}
        finally:
            self.active.pop(session.start.device_id, None)
            self.streams.pop(session_id, None)
            self.partial_text.pop(session_id, None)
            self.state[session_id] = VoiceState.LISTENING if self.state.get(session_id) != VoiceState.COMPLETED else VoiceState.COMPLETED

    def cancel(self, session_id):
        self.processor.cancel(session_id)
        self.streams.pop(session_id, None)
        self.partial_text.pop(session_id, None)
        self.state[session_id] = VoiceState.LISTENING
        for device, active in list(self.active.items()):
            if active == session_id:
                self.active.pop(device, None)
