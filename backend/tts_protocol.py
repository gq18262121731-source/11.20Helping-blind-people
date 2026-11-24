"""Versioned binary frames for the independent TTS downlink WebSocket."""

from __future__ import annotations

import struct


MAGIC = b"TS"
VERSION = 1
START = 1
CHUNK = 2
END = 3
CANCEL = 4
READY = 0x81
PLAYBACK_STARTED = 0x82
PLAYBACK_DONE = 0x83
PLAYBACK_ERROR = 0x84
PLAYBACK_CANCELLED = 0x85

START_HEADER = struct.Struct("<2sBBIII")  # magic, version, type, request, rate, total bytes
CHUNK_HEADER = struct.Struct("<2sBBII")  # magic, version, type, request, chunk sequence
CONTROL_HEADER = struct.Struct("<2sBBII")  # magic, version, type, request, value/code
MAX_AUDIO_CHUNK_BYTES = 8192
STREAMING_AUDIO_BYTES = 0xFFFFFFFF


def speech_command(text: str) -> bytes:
    """Build the legacy LU6288 text command still used by the camera WS link."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("speech text cannot be empty")
    content = text.strip().encode("gbk")
    if len(content) > 240:
        raise ValueError("speech text is too long")
    return b"T<G>" + content


class TtsProtocolError(ValueError):
    """A malformed TTS downlink or playback status frame."""


def _request_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise TtsProtocolError("request_id must be an unsigned 32-bit integer")
    return value


def pack_start(request_id: int, sample_rate: int, total_bytes: int) -> bytes:
    request_id = _request_id(request_id)
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
        raise TtsProtocolError("sample_rate must be between 8000 and 48000")
    if (isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or
            (total_bytes != STREAMING_AUDIO_BYTES and (total_bytes <= 0 or total_bytes % 2))):
        raise TtsProtocolError("total_bytes must be a positive even integer or the streaming sentinel")
    return START_HEADER.pack(MAGIC, VERSION, START, request_id, sample_rate, total_bytes)


def pack_chunk(request_id: int, sequence: int, pcm: bytes) -> bytes:
    request_id = _request_id(request_id)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFFFFFF:
        raise TtsProtocolError("chunk sequence must be an unsigned 32-bit integer")
    if not isinstance(pcm, bytes) or not pcm or len(pcm) > MAX_AUDIO_CHUNK_BYTES or len(pcm) % 2:
        raise TtsProtocolError("PCM chunk must contain 1-8192 even bytes")
    return CHUNK_HEADER.pack(MAGIC, VERSION, CHUNK, request_id, sequence) + pcm


def pack_control(frame_type: int, request_id: int, value: int = 0) -> bytes:
    request_id = _request_id(request_id)
    if frame_type not in (END, CANCEL, READY, PLAYBACK_STARTED, PLAYBACK_DONE, PLAYBACK_ERROR, PLAYBACK_CANCELLED):
        raise TtsProtocolError("unknown TTS control frame type")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise TtsProtocolError("control value must be an unsigned 32-bit integer")
    return CONTROL_HEADER.pack(MAGIC, VERSION, frame_type, request_id, value)


def unpack_status(frame: bytes) -> dict:
    if not isinstance(frame, bytes) or len(frame) != CONTROL_HEADER.size:
        raise TtsProtocolError("TTS status frame must be 12 bytes")
    magic, version, frame_type, request_id, value = CONTROL_HEADER.unpack(frame)
    if magic != MAGIC or version != VERSION:
        raise TtsProtocolError("invalid TTS status frame header")
    if frame_type not in (READY, PLAYBACK_STARTED, PLAYBACK_DONE, PLAYBACK_ERROR, PLAYBACK_CANCELLED):
        raise TtsProtocolError("frame is not a TTS playback status")
    return {"type": frame_type, "request_id": request_id, "value": value}
