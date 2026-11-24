import asyncio
import hashlib
import json
from pathlib import Path
import wave

import pytest

from backend.tts_delivery import TtsDelivery
from backend.tts_library import TtsLibrary
from backend.tts_protocol import (
    CHUNK,
    END,
    CANCEL,
    PLAYBACK_DONE,
    PLAYBACK_CANCELLED,
    PLAYBACK_STARTED,
    START,
    pack_control,
)


def make_library(root, phrases, *, sample_rate=16_000):
    audio_dir = root / "revisions" / "delivery-test"
    audio_dir.mkdir(parents=True)
    rows = []
    for phrase_id, text in phrases:
        path = audio_dir / f"{phrase_id}.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"\x00\x00" * (sample_rate * 80 // 1000))
        rows.append({
            "phrase_id": phrase_id,
            "text": text,
            "path": f"revisions/delivery-test/{phrase_id}.wav",
            "duration_ms": 80,
            "byte_length": sample_rate * 80 // 1000 * 2,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    (root / "manifest.json").write_text(json.dumps({
        "version": 1,
        "voice": "zh-CN-XiaoxiaoNeural",
        "audio": {"format": "pcm_s16le", "sample_rate": sample_rate, "channels": 1},
        "phrases": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return TtsLibrary(root)


class FakeWebSocket:
    def __init__(self):
        self.json_frames = []
        self.binary_frames = []
        self.status_events = []
        self.peer = None
        self.hub = None

    async def accept(self):
        pass

    async def close(self, code=1000):
        pass

    async def send_json(self, payload):
        self.json_frames.append(payload)

    async def send_bytes(self, payload):
        self.binary_frames.append(payload)
        frame_type = payload[3]
        request_id = int.from_bytes(payload[4:8], "little")
        if frame_type == START:
            status = await self.hub.receive(self.peer, pack_control(PLAYBACK_STARTED, request_id))
            if status:
                self.status_events.append(status)
                await self.hub.publish_task_status(status)
        elif frame_type == END:
            status = await self.hub.receive(self.peer, pack_control(PLAYBACK_DONE, request_id))
            if status:
                self.status_events.append(status)
                await self.hub.publish_task_status(status)
        elif frame_type == CANCEL:
            status = await self.hub.receive(self.peer, pack_control(PLAYBACK_CANCELLED, request_id))
            if status:
                self.status_events.append(status)
                await self.hub.publish_task_status(status)


def test_delivery_streams_binary_pcm_on_independent_peer(tmp_path):
    async def exercise():
        library = make_library(tmp_path, [("front_person", "前方有行人，请注意。")])
        hub = TtsDelivery(library)
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer

        assert hub.queue_text("hat-001", "前方有行人，请注意。", priority=0) == {
            "status": "queued", "phrase_id": "front_person",
        }
        await asyncio.wait_for(peer.queue.join(), timeout=2)

        assert websocket.json_frames[0]["type"] == "tts_connected"
        assert [frame[3] for frame in websocket.binary_frames] == [START, CHUNK, CHUNK, END]
        assert b"".join(frame[12:] for frame in websocket.binary_frames if frame[3] == CHUNK) == b"\x00\x00" * (16_000 * 80 // 1000)
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_delivery_rejects_phrase_with_unsupported_device_sample_rate(tmp_path):
    async def exercise():
        library = make_library(
            tmp_path, [("front_person", "前方有行人，请注意。")], sample_rate=24_000,
        )
        hub = TtsDelivery(library)
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer

        assert hub.queue_text("hat-001", "前方有行人，请注意。") == {
            "status": "sample_rate_mismatch",
        }
        assert peer.queue.empty()
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_delivery_reports_offline_missing_and_bad_priority(tmp_path):
    async def exercise():
        library = make_library(tmp_path, [("front_person", "前方有行人，请注意。")])
        hub = TtsDelivery(library)

        assert hub.queue_text("hat-001", "前方有行人，请注意。") == {"status": "device_offline"}
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer
        assert hub.queue_text("hat-001", "未收录的动态文本") == {"status": "synthesis_unavailable"}
        peer.output_ready = False
        assert hub.queue_phrase("hat-001", "front_person") == {"status": "output_unconfigured"}
        peer.output_ready = None
        assert hub.queue_phrase("hat-001", "front_person", priority=True) == {"status": "invalid_priority"}
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_delivery_reports_playback_state_for_multimodal_task(tmp_path):
    async def exercise():
        library = make_library(tmp_path, [("front_person", "前方有行人，请注意。")])
        hub = TtsDelivery(library)
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer
        queued = hub.queue_text("hat-001", "前方有行人，请注意。", task_id=37)
        assert queued["status"] == "queued"
        assert queued["task_id"] == 37
        await asyncio.wait_for(peer.queue.join(), timeout=2)
        states = [item["status"] for item in websocket.status_events]
        assert states == ["speaking", "completed"]
        assert all(item["task_id"] == 37 for item in websocket.status_events)
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_stop_clears_queued_tasks_and_marks_them_interrupted(tmp_path):
    async def exercise():
        library = make_library(tmp_path, [("front_person", "前方有行人，请注意。")])
        hub = TtsDelivery(library)
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer
        statuses = []
        hub.on_task_status = statuses.append
        hub.queue_text("hat-001", "前方有行人，请注意。", task_id=51)
        result = hub.cancel_device("hat-001")
        assert result["status"] == "cancel_requested"
        assert peer.queue.empty()
        await asyncio.sleep(0)
        assert statuses[0]["task_id"] == 51
        assert statuses[0]["status"] == "interrupted"
        await hub.disconnect(peer)

    asyncio.run(exercise())


class FakeSynthesizer:
    voice = "zh-CN-XiaoxiaoNeural"
    sample_rate = 16_000

    async def synthesize_wav(self, text, output_path: Path):
        await asyncio.sleep(0)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(b"\x01\x00" * 3200)

    async def stream_pcm(self, text, *, output_rate=None):
        yield b"\x01\x00" * 3200


def test_dynamic_reply_synthesizes_caches_sends_and_records_playback_ack(tmp_path):
    async def exercise():
        library = TtsLibrary(tmp_path / "library")
        hub = TtsDelivery(library, synthesizer=FakeSynthesizer())
        statuses = []
        hub.on_task_status = statuses.append
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer

        result = hub.queue_text("hat-001", "请前往前方的出口。", task_id=87)
        assert result == {"status": "synthesizing", "task_id": 87}
        for _ in range(100):
            if hub._synthesis_counts.get("hat-001", 0) == 0:
                break
            await asyncio.sleep(.01)
        assert hub._synthesis_counts.get("hat-001", 0) == 0

        phrase = library.get_by_text("请前往前方的出口。")
        assert phrase is not None and phrase.phrase_id.startswith("dyn-")
        assert phrase.sample_rate == 16_000
        assert [event["status"] for event in statuses] == ["queued", "speaking", "completed"]
        assert [frame[3] for frame in websocket.binary_frames] == [START, CHUNK, CHUNK, CHUNK, CHUNK, CHUNK, END]
        assert b"".join(frame[12:] for frame in websocket.binary_frames if frame[3] == CHUNK) == b"\x01\x00" * 3200
        assert TtsLibrary(tmp_path / "library").get_by_text("请前往前方的出口。").phrase_id == phrase.phrase_id
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_dynamic_synthesis_cancel_does_not_enqueue_late_reply(tmp_path):
    class SlowSynthesizer(FakeSynthesizer):
        async def synthesize_wav(self, text, output_path):
            await asyncio.sleep(30)

        async def stream_pcm(self, text, *, output_rate=None):
            await asyncio.sleep(30)
            yield b"\x01\x00" * 3200

    async def exercise():
        library = TtsLibrary(tmp_path / "library")
        hub = TtsDelivery(library, synthesizer=SlowSynthesizer())
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer
        assert hub.queue_text("hat-001", "这条语音应该被取消", task_id=90)["status"] == "synthesizing"
        await asyncio.sleep(0)
        assert hub.cancel_device("hat-001")["status"] == "cancel_requested"
        await asyncio.sleep(.01)
        assert peer.queue.empty()
        assert library.available is False
        await hub.disconnect(peer)

    asyncio.run(exercise())


def test_urgent_cached_phrase_cancels_active_dynamic_reply(tmp_path):
    async def exercise():
        library = make_library(tmp_path / "library", [("urgent_warning", "前方有车，请注意！")], sample_rate=16_000)
        hub = TtsDelivery(library, synthesizer=FakeSynthesizer())
        lifecycle_events = []
        hub.on_task_status = lifecycle_events.append
        websocket = FakeWebSocket()
        websocket.hub = hub
        peer = await hub.connect("hat-001", websocket)
        websocket.peer = peer

        assert hub.queue_text("hat-001", "请稍微向左移动。", task_id=101)["status"] == "synthesizing"
        for _ in range(100):
            if any(frame[3] == CHUNK for frame in websocket.binary_frames):
                break
            await asyncio.sleep(.005)
        assert any(frame[3] == CHUNK for frame in websocket.binary_frames)
        assert hub.queue_phrase("hat-001", "urgent_warning", priority=0)["status"] == "queued"
        for _ in range(100):
            if hub._synthesis_counts.get("hat-001", 0) == 0 and peer.queue.empty() and peer.active_idle.is_set():
                break
            await asyncio.sleep(.01)

        frame_types = [frame[3] for frame in websocket.binary_frames]
        assert CANCEL in frame_types
        assert frame_types.count(START) == 2
        assert frame_types[-1] == END
        assert [event["status"] for event in websocket.status_events if event.get("task_id") == 101] == [
            "speaking", "interrupted",
        ]
        assert [event["status"] for event in lifecycle_events if event.get("task_id") == 101] == [
            "queued", "speaking", "interrupted",
        ]
        await hub.disconnect(peer)

    asyncio.run(exercise())
