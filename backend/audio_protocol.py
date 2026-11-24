"""Wire format and validation for the independent ``/ws/audio`` channel.

Control messages are JSON and PCM samples are WebSocket binary frames.  A
binary frame may use the same QH header as the camera transport with
``frame_type=2``; raw PCM is accepted for local simulators after a session has
been opened.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"QH"
VERSION = 1
FRAME_TYPE_AUDIO = 2
HEADER_LEN = 26
PCM_SAMPLE_RATE = 16_000
PCM_BITS = 16
PCM_CHANNELS = 1
HEADER = struct.Struct("<2sBBHIQHHI")


class AudioProtocolError(ValueError):
    """A malformed control message or binary audio frame."""


@dataclass(frozen=True)
class AudioStart:
    device_id: str
    session_id: str
    source_id: str = ""
    source_mode: str = "hardware"
    frame_seq: int | None = None
    tts_target: str = "device"
    input_source: str = "hardware"
    sample_rate: int = PCM_SAMPLE_RATE
    channels: int = PCM_CHANNELS
    bits_per_sample: int = PCM_BITS

    @classmethod
    def from_message(cls, message: dict) -> "AudioStart":
        if message.get("type") != "audio_start":
            raise AudioProtocolError("需要 audio_start 消息")
        device_id = str(message.get("device_id", "")).strip()
        session_id = str(message.get("session_id", "")).strip()
        if not device_id or len(device_id) > 80 or not session_id or len(session_id) > 120:
            raise AudioProtocolError("device_id 或 session_id 无效")
        source_id = str(message.get("source_id") or device_id).strip()
        source_mode = str(message.get("source_mode") or "hardware").strip()
        tts_target = str(message.get("tts_target") or ("browser" if source_mode == "software" else "device")).strip()
        input_source = str(message.get("input_source") or "hardware").strip()
        if len(source_id) > 80 or source_mode not in {"hardware", "software", "simulation"}:
            raise AudioProtocolError("音频来源字段无效")
        if tts_target not in {"browser", "device", "simulation"}:
            raise AudioProtocolError("音频朗读目标无效")
        if input_source not in {"computer", "hardware"}:
            raise AudioProtocolError("不支持的麦克风输入来源")
        if input_source == "computer" and device_id != "browser_mic":
            raise AudioProtocolError("电脑麦克风会话标识无效")
        if input_source == "hardware" and (source_mode != "hardware" or source_id != device_id or tts_target != "device"):
            raise AudioProtocolError("帽端麦克风必须绑定同一帽端的播放通道")
        if source_mode == "software" and tts_target != "browser":
            raise AudioProtocolError("电脑画面任务必须使用本机朗读")
        if source_mode == "hardware" and tts_target != "device":
            raise AudioProtocolError("帽端画面任务必须使用帽端朗读")
        frame_seq = message.get("frame_seq")
        if frame_seq is not None:
            try:
                frame_seq = int(frame_seq)
            except (TypeError, ValueError) as error:
                raise AudioProtocolError("frame_seq 必须为整数") from error
        try:
            values = (int(message.get("sample_rate", PCM_SAMPLE_RATE)),
                      int(message.get("channels", PCM_CHANNELS)),
                      int(message.get("bits_per_sample", PCM_BITS)))
        except (TypeError, ValueError) as error:
            raise AudioProtocolError("音频格式字段必须是整数") from error
        if values != (PCM_SAMPLE_RATE, PCM_CHANNELS, PCM_BITS):
            raise AudioProtocolError("仅支持 16kHz、16bit、单声道 PCM")
        return cls(device_id, session_id, source_id, source_mode, frame_seq, tts_target, input_source, *values)


def unpack_audio_frame(data: bytes, max_payload: int = 64_000) -> tuple[int, bytes]:
    """Return sequence and PCM bytes from a QH audio frame or raw PCM frame."""
    if data.startswith(MAGIC):
        if len(data) < HEADER_LEN:
            raise AudioProtocolError("音频帧头不完整")
        magic, version, frame_type, header_len, seq, _timestamp, index, total, length = HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION or frame_type != FRAME_TYPE_AUDIO or header_len != HEADER_LEN:
            raise AudioProtocolError("音频帧头无效")
        if total < 1 or index >= total or length > max_payload or len(data) != header_len + length:
            raise AudioProtocolError("音频帧长度或分片字段无效")
        payload = data[header_len:]
    else:
        seq, payload = 0, data
        if len(payload) > max_payload:
            raise AudioProtocolError("音频分块过大")
    if not payload or len(payload) % 2:
        raise AudioProtocolError("PCM 数据必须为非空偶数字节")
    return seq, bytes(payload)


def pack_audio_frame(payload: bytes, seq: int, timestamp_ms: int, *, chunk_index=0, chunk_total=1) -> bytes:
    if not payload or len(payload) % 2 or len(payload) > 64_000:
        raise AudioProtocolError("PCM 数据无效")
    header = HEADER.pack(MAGIC, VERSION, FRAME_TYPE_AUDIO, HEADER_LEN, int(seq), int(timestamp_ms),
                         int(chunk_index), int(chunk_total), len(payload))
    return header + payload
