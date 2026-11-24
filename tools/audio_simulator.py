"""Upload a WAV file as if it came from MAX9814/ESP32-S3."""
import asyncio, json, os, struct, sys, time, wave
import websockets

URL = os.getenv("QH_AUDIO_WS", "ws://127.0.0.1:8765/ws/audio")
DEVICE_ID = os.getenv("QH_DEVICE_ID", "simulator_001")

def frame(payload, seq):
    return (b"QH" + bytes([1, 2]) + struct.pack("<H", 26) + struct.pack("<I", seq) +
            struct.pack("<Q", int(time.time() * 1000)) + struct.pack("<HHI", 0, 1, len(payload)) + payload)

async def main(path):
    with wave.open(path, "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise SystemExit("WAV 必须是 16kHz、16bit、单声道")
        pcm = wav.readframes(wav.getnframes())
    session = f"sim-{int(time.time() * 1000)}"
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"type": "audio_start", "device_id": DEVICE_ID, "session_id": session,
                       "sample_rate": 16000, "channels": 1, "bits_per_sample": 16}))
        await ws.recv()
        for seq, offset in enumerate(range(0, len(pcm), 640), 1):
            await ws.send(frame(pcm[offset:offset + 640], seq))
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({"type": "audio_end", "session_id": session}))
        print(await ws.recv())

if __name__ == "__main__":
    if len(sys.argv) != 2: raise SystemExit("用法: python tools/audio_simulator.py sample.wav")
    asyncio.run(main(sys.argv[1]))
