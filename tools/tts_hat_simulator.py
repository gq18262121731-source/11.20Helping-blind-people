"""Simulate the ESP32 side of the independent TTS playback WebSocket."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import struct
import sys
import wave

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tts_protocol import (
    CANCEL,
    CHUNK,
    END,
    PLAYBACK_CANCELLED,
    PLAYBACK_DONE,
    PLAYBACK_ERROR,
    PLAYBACK_STARTED,
    READY,
    START,
    STREAMING_AUDIO_BYTES,
    VERSION,
    pack_control,
)


START_HEADER = struct.Struct("<2sBBIII")
CHUNK_HEADER = struct.Struct("<2sBBII")
CONTROL_HEADER = struct.Struct("<2sBBII")
MAX_AUDIO_BYTES = 8 * 1024 * 1024


def decode_control(frame: bytes) -> tuple[int, int, int]:
    if len(frame) != CONTROL_HEADER.size:
        raise ValueError("control frame must be 12 bytes")
    magic, version, frame_type, request_id, value = CONTROL_HEADER.unpack(frame)
    if magic != b"TS" or version != VERSION:
        raise ValueError("bad TTS control header")
    return frame_type, request_id, value


async def run(url: str, output_dir: Path, *, play: bool = False, once: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    active_request = None
    sample_rate = 0
    expected_bytes = 0
    next_sequence = 0
    pcm = bytearray()

    async with websockets.connect(url, max_size=16_384, ping_interval=20, ping_timeout=20) as socket:
        print(f"Connected to {url}")
        print("Waiting for a reply; this is a software playback simulator.")
        await socket.send(pack_control(READY, 0))
        while True:
            incoming = await socket.recv()
            if isinstance(incoming, str):
                print(incoming)
                continue
            if len(incoming) < 4 or incoming[:2] != b"TS" or incoming[2] != VERSION:
                await socket.send(pack_control(PLAYBACK_ERROR, active_request or 0, 3))
                print("Rejected a malformed TTS frame")
                continue

            frame_type = incoming[3]
            if frame_type == START:
                if len(incoming) != START_HEADER.size:
                    await socket.send(pack_control(PLAYBACK_ERROR, 0, 3))
                    continue
                _magic, _version, _kind, request_id, sample_rate, expected_bytes = START_HEADER.unpack(incoming)
                if (not 8_000 <= sample_rate <= 48_000 or
                        not (expected_bytes == STREAMING_AUDIO_BYTES or
                             (0 < expected_bytes <= MAX_AUDIO_BYTES and expected_bytes % 2 == 0))):
                    await socket.send(pack_control(PLAYBACK_ERROR, request_id, 3))
                    continue
                active_request = request_id
                next_sequence = 0
                pcm = bytearray()
                print(f"Starting request {request_id}: {sample_rate} Hz, {expected_bytes} PCM bytes")
                await socket.send(pack_control(PLAYBACK_STARTED, request_id))
                continue

            if frame_type == CHUNK:
                if len(incoming) <= CHUNK_HEADER.size or active_request is None:
                    await socket.send(pack_control(PLAYBACK_ERROR, active_request or 0, 3))
                    continue
                _magic, _version, _kind, request_id, sequence = CHUNK_HEADER.unpack_from(incoming)
                chunk = incoming[CHUNK_HEADER.size:]
                if (request_id != active_request or sequence != next_sequence or len(chunk) % 2 or
                        len(pcm) + len(chunk) > MAX_AUDIO_BYTES or
                        (expected_bytes != STREAMING_AUDIO_BYTES and len(pcm) + len(chunk) > expected_bytes)):
                    await socket.send(pack_control(PLAYBACK_ERROR, active_request, 3))
                    active_request = None
                    pcm.clear()
                    continue
                pcm.extend(chunk)
                next_sequence += 1
                continue

            if frame_type in (END, CANCEL):
                try:
                    _kind, request_id, value = decode_control(incoming)
                except ValueError:
                    await socket.send(pack_control(PLAYBACK_ERROR, active_request or 0, 3))
                    continue
                if request_id != active_request:
                    continue
                if frame_type == CANCEL:
                    active_request = None
                    pcm.clear()
                    await socket.send(pack_control(PLAYBACK_CANCELLED, request_id))
                    print(f"Cancelled request {request_id}")
                    continue
                if (value != next_sequence or not pcm or
                        (expected_bytes != STREAMING_AUDIO_BYTES and len(pcm) != expected_bytes)):
                    await socket.send(pack_control(PLAYBACK_ERROR, request_id, 3))
                    active_request = None
                    pcm.clear()
                    continue

                created = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                wav_path = output_dir / f"reply-{created}.wav"
                with wave.open(str(wav_path), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(sample_rate)
                    output.writeframes(pcm)
                if play:
                    if os.name != "nt":
                        raise RuntimeError("--play currently requires Windows winsound")
                    import winsound
                    await asyncio.to_thread(winsound.PlaySound, str(wav_path), winsound.SND_FILENAME)
                print(f"Playback simulated; PCM saved to {wav_path}")
                await socket.send(pack_control(PLAYBACK_DONE, request_id, next_sequence))
                active_request = None
                pcm.clear()
                if once:
                    return


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate a Qiming Hat TTS playback device")
    parser.add_argument("--url", default=os.getenv("TTS_WS_URL", "ws://127.0.0.1:8765/ws/tts"))
    parser.add_argument("--device-id", default="esp32s3_cam_001")
    parser.add_argument("--output", type=Path, default=ROOT / "backend" / "data" / "tts" / "simulator-output")
    parser.add_argument("--play", action="store_true", help="also play through this PC's speakers")
    parser.add_argument("--once", action="store_true", help="exit after the next completed reply")
    args = parser.parse_args()
    url = args.url + ("&" if "?" in args.url else "?") + "device_id=" + args.device_id
    try:
        asyncio.run(run(url, args.output, play=args.play, once=args.once))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"TTS simulator failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
