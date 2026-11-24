"""Dedicated WebSocket delivery for pre-generated, local speech assets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
from itertools import count
import secrets
from pathlib import Path
from hashlib import sha256
from functools import wraps
from tempfile import TemporaryDirectory
import threading
import wave
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket

from .tts_library import TtsLibrary, TtsLibraryError, TtsPhrase
from .tts_synthesis import EdgeTtsSynthesizer, TtsSynthesisError
from .tts_protocol import (
    CANCEL,
    END,
    PLAYBACK_DONE,
    PLAYBACK_ERROR,
    PLAYBACK_CANCELLED,
    PLAYBACK_STARTED,
    READY,
    TtsProtocolError,
    pack_chunk,
    pack_control,
    pack_start,
    STREAMING_AUDIO_BYTES,
    unpack_status,
)


DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DEFAULT_QUEUE_SIZE = 16
DEFAULT_CHUNK_MS = 40


def _asset_locked(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._asset_lock:
            return method(self, *args, **kwargs)
    return guarded


@dataclass(order=True)
class _QueuedPhrase:
    priority: int
    order: int
    request_id: int = field(compare=False)
    phrase: TtsPhrase = field(compare=False)
    task_id: int | None = field(compare=False, default=None)
    generation: int = field(compare=False, default=0)
    ready: asyncio.Event | None = field(compare=False, default=None)


@dataclass
class TtsPeer:
    device_id: str
    websocket: WebSocket
    queue: asyncio.PriorityQueue = field(default_factory=lambda: asyncio.PriorityQueue(DEFAULT_QUEUE_SIZE))
    worker: asyncio.Task | None = None
    active_request_id: int | None = None
    active_task_id: int | None = None
    active_priority: int | None = None
    active_phrase: TtsPhrase | None = None
    generation: int = 0
    cancel_active: asyncio.Event = field(default_factory=asyncio.Event)
    playback_started: asyncio.Event = field(default_factory=asyncio.Event)
    playback_done: asyncio.Event = field(default_factory=asyncio.Event)
    playback_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    playback_error: int | None = None
    active_idle: asyncio.Event = field(default_factory=asyncio.Event)
    stream_pending: bool = False
    worker_busy: bool = False
    output_ready: bool | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_report: dict[str, Any] | None = None
    closed: bool = False


class TtsDelivery:
    """Per-device priority queues isolated from camera and microphone sockets."""

    def __init__(
        self, library: TtsLibrary, *, chunk_ms: int = DEFAULT_CHUNK_MS,
        synthesizer: EdgeTtsSynthesizer | None = None, device_sample_rate: int = 16_000,
    ):
        self.library = library
        self.chunk_ms = chunk_ms
        self.synthesizer = synthesizer
        self.device_sample_rate = device_sample_rate
        self.peers: dict[str, TtsPeer] = {}
        self._orders = count()
        self.on_task_status = None
        self._library_write_lock = asyncio.Lock()
        self._asset_lock = threading.RLock()
        self._synthesis_jobs: set[asyncio.Task] = set()
        self._synthesis_task_info: dict[asyncio.Task, tuple[str, int | None, int]] = {}
        self._synthesis_counts: dict[str, int] = {}
        self._device_generation: dict[str, int] = {}
        self._published_task_states: set[tuple[str, int, str]] = set()

    async def publish_task_status(self, event: dict[str, Any]) -> None:
        task_id = event.get("task_id")
        status = event.get("status")
        if event.get("type") == "tts_playback_status" and task_id is not None and status:
            key = (str(event.get("device_id") or ""), int(task_id), str(status))
            if key in self._published_task_states:
                return
            self._published_task_states.add(key)
            if len(self._published_task_states) > 4096:
                self._published_task_states.pop()
        callback = self.on_task_status
        if callback is not None:
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result

    async def connect(self, device_id: str, websocket: WebSocket) -> TtsPeer:
        if not DEVICE_ID.fullmatch(device_id):
            raise ValueError("invalid device_id")
        await websocket.accept()
        previous = self.peers.get(device_id)
        if previous:
            await self.disconnect(previous, close_code=1012)
        peer = TtsPeer(device_id=device_id, websocket=websocket)
        peer.active_idle.set()
        self.peers[device_id] = peer
        peer.worker = asyncio.create_task(self._worker(peer), name=f"tts-{device_id}")
        await self._send_json(peer, {"type": "tts_connected", "device_id": device_id, "protocol_version": 1})
        return peer

    async def disconnect(self, peer: TtsPeer, *, close_code: int | None = None) -> None:
        if peer.closed:
            return
        peer.closed = True
        peer.cancel_active.set()
        self._device_generation[peer.device_id] = self._device_generation.get(peer.device_id, 0) + 1
        for job in tuple(self._synthesis_jobs):
            if job.get_name() == f"tts-synthesis-{peer.device_id}":
                job.cancel()
        if self.peers.get(peer.device_id) is peer:
            self.peers.pop(peer.device_id, None)
        if peer.worker and peer.worker is not asyncio.current_task():
            peer.worker.cancel()
            try:
                await peer.worker
            except asyncio.CancelledError:
                pass
        if close_code is not None:
            try:
                await peer.websocket.close(code=close_code)
            except (RuntimeError, OSError):
                pass

    @_asset_locked
    def queue_text(self, device_id: str, text: str, *, priority: int = 1, task_id: int | None = None) -> dict[str, Any]:
        peer = self.peers.get(device_id)
        if peer is None or peer.closed:
            return {"status": "device_offline"}
        if peer.output_ready is False:
            return {"status": "output_unconfigured"}
        text = str(text or "").strip()
        if not text:
            return {"status": "empty_text"}
        if len(text) > 500:
            return {"status": "text_too_long"}
        try:
            phrase = self.library.get_by_text(text)
        except TtsLibraryError:
            return {"status": "library_error"}
        if phrase is not None:
            return self.queue_phrase(device_id, phrase.phrase_id, priority=priority, task_id=task_id)
        if self.synthesizer is None:
            return {"status": "synthesis_unavailable"}
        if not getattr(self.synthesizer, "available", True):
            return {"status": "synthesis_unavailable"}
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 9:
            return {"status": "invalid_priority"}
        if priority == 0:
            self._preempt_lower_priority(peer)
        if self._synthesis_counts.get(device_id, 0) >= 4:
            return {"status": "synthesis_queue_full"}
        generation = self._device_generation.get(device_id, 0)
        stream_now = (peer.active_request_id is None and peer.queue.empty() and
                      peer.active_idle.is_set() and not peer.stream_pending and not peer.worker_busy)
        request_id = secrets.randbits(32) if stream_now else None
        if stream_now:
            peer.stream_pending = True
            peer.active_idle.clear()
        job = asyncio.create_task(
            (self._stream_and_play(device_id, text, priority, task_id, generation, request_id)
             if stream_now else
             self._synthesize_and_queue(device_id, text, priority, task_id, generation)),
            name=f"tts-synthesis-{device_id}",
        )
        self._synthesis_jobs.add(job)
        self._synthesis_task_info[job] = (device_id, task_id, priority)
        self._synthesis_counts[device_id] = self._synthesis_counts.get(device_id, 0) + 1
        job.add_done_callback(self._synthesis_jobs.discard)
        job.add_done_callback(self._finish_synthesis_job)
        if stream_now:
            job.add_done_callback(lambda _job, target=peer: self._finish_stream_reservation(target))
        result = {"status": "synthesizing"}
        if task_id is not None:
            result["task_id"] = task_id
        return result

    def _finish_synthesis_job(self, job: asyncio.Task) -> None:
        device_id, _task_id, _priority = self._synthesis_task_info.pop(job, (None, None, 0))
        if device_id is not None:
            self._synthesis_counts[device_id] = max(0, self._synthesis_counts.get(device_id, 1) - 1)

    @staticmethod
    def _finish_stream_reservation(peer: TtsPeer) -> None:
        peer.stream_pending = False
        if peer.active_request_id is None:
            peer.active_idle.set()

    async def _synthesize_and_queue(
        self, device_id: str, text: str, priority: int, task_id: int | None, generation: int,
    ) -> None:
        peer = self.peers.get(device_id)
        try:
            with TemporaryDirectory(prefix="qiming-tts-") as directory:
                wav_path = Path(directory) / "reply.wav"
                await self.synthesizer.synthesize_wav(text, wav_path)
                if generation != self._device_generation.get(device_id, 0) or not peer or peer.closed:
                    await self._publish_synthesis_status(device_id, task_id, "interrupted")
                    return
                phrase_id = "dyn-" + sha256(
                    f"{self.synthesizer.voice}\0{self.synthesizer.sample_rate}\0{text}".encode("utf-8")
                ).hexdigest()[:32]
                async with self._library_write_lock:
                    phrase = await asyncio.to_thread(
                        self._cache_generated_phrase,
                        phrase_id, text, self.synthesizer.voice, wav_path,
                        self.synthesizer.sample_rate,
                    )
            if generation != self._device_generation.get(device_id, 0) or not peer or peer.closed:
                await self._publish_synthesis_status(device_id, task_id, "interrupted")
                return
            start_gate = asyncio.Event()
            result = self.queue_phrase(
                device_id, phrase.phrase_id, priority=priority, task_id=task_id, ready=start_gate,
            )
            if result.get("status") == "queued":
                try:
                    if task_id is not None:
                        await self.publish_task_status({
                            "type": "tts_playback_status", "device_id": device_id,
                            "request_id": result["request_id"], "task_id": task_id, "status": "queued",
                        })
                finally:
                    start_gate.set()
            elif result.get("status") != "queued":
                await self._publish_synthesis_status(device_id, task_id, result.get("status", "failed"))
        except asyncio.CancelledError:
            await self._publish_synthesis_status(device_id, task_id, "interrupted")
            raise
        except (TtsSynthesisError, TtsLibraryError, OSError) as error:
            await self._publish_synthesis_status(device_id, task_id, "failed", str(error)[:160])
            if peer and not peer.closed:
                try:
                    await self._send_json(peer, {"type": "tts_synthesis_error", "detail": str(error)[:160]})
                except (RuntimeError, OSError):
                    pass
        except Exception as error:
            await self._publish_synthesis_status(device_id, task_id, "failed", type(error).__name__)

    async def _stream_and_play(
        self, device_id: str, text: str, priority: int, task_id: int | None,
        generation: int, request_id: int | None,
    ) -> None:
        """Stream a cache-miss reply directly to an idle hat while Edge TTS is producing it."""
        peer = self.peers.get(device_id)
        if not peer or peer.closed or generation != self._device_generation.get(device_id, 0):
            if peer is not None:
                peer.active_idle.set()
            await self._publish_synthesis_status(device_id, task_id, "interrupted")
            return
        peer.stream_pending = False
        peer.active_idle.clear()
        request_id = request_id if request_id is not None else secrets.randbits(32)
        peer.playback_started.clear()
        peer.playback_done.clear()
        peer.playback_cancelled.clear()
        peer.playback_error = None
        peer.active_request_id = request_id
        peer.active_task_id = task_id
        peer.active_priority = priority
        peer.active_idle.clear()
        total_bytes = 0
        sequence = 0
        try:
            if task_id is not None:
                await self.publish_task_status({
                    "type": "tts_playback_status", "device_id": device_id,
                    "request_id": request_id, "task_id": task_id, "status": "queued",
                })
            await self._send_bytes(peer, pack_start(request_id, self.device_sample_rate, STREAMING_AUDIO_BYTES))
            started_task = asyncio.create_task(peer.playback_started.wait())
            cancel_task = asyncio.create_task(peer.cancel_active.wait())
            try:
                completed, _ = await asyncio.wait(
                    {started_task, cancel_task}, timeout=3.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not completed:
                    raise TtsSynthesisError("帽端未确认 TTS 播放准备完成")
                if cancel_task in completed and peer.cancel_active.is_set():
                    await self._cancel_phrase(peer, request_id)
                    await self._publish_synthesis_status(device_id, task_id, "interrupted")
                    return
            finally:
                for wait_task in (started_task, cancel_task):
                    if not wait_task.done():
                        wait_task.cancel()
                await asyncio.gather(started_task, cancel_task, return_exceptions=True)

            with TemporaryDirectory(prefix="qiming-tts-stream-") as directory:
                wav_path = Path(directory) / "reply.wav"
                with wave.open(str(wav_path), "wb") as cache_wav:
                    cache_wav.setnchannels(1)
                    cache_wav.setsampwidth(2)
                    cache_wav.setframerate(self.device_sample_rate)
                    chunk_buffer = bytearray()
                    async for pcm in self.synthesizer.stream_pcm(text, output_rate=self.device_sample_rate):
                        if peer.cancel_active.is_set() or peer.closed:
                            await self._cancel_phrase(peer, request_id)
                            await self._publish_synthesis_status(device_id, task_id, "interrupted")
                            return
                        if len(pcm) & 1:
                            pcm = pcm[:-1]
                        if pcm:
                            cache_wav.writeframesraw(pcm)
                        chunk_buffer.extend(pcm)
                        while len(chunk_buffer) >= max(2, self.device_sample_rate * self.chunk_ms // 1000 * 2):
                            chunk = bytes(chunk_buffer[:self.device_sample_rate * self.chunk_ms // 1000 * 2])
                            del chunk_buffer[:len(chunk)]
                            total_bytes += len(chunk)
                            if total_bytes > self.device_sample_rate * 2 * 30:
                                raise TtsSynthesisError("语音超过 30 秒播放上限")
                            await self._send_bytes(peer, pack_chunk(request_id, sequence, chunk))
                            sequence += 1
                            await asyncio.sleep(len(chunk) / (self.device_sample_rate * 2))
                    if chunk_buffer:
                        if len(chunk_buffer) & 1:
                            chunk_buffer = chunk_buffer[:-1]
                        if chunk_buffer:
                            chunk = bytes(chunk_buffer)
                            total_bytes += len(chunk)
                            if total_bytes > self.device_sample_rate * 2 * 30:
                                raise TtsSynthesisError("语音超过 30 秒播放上限")
                            await self._send_bytes(peer, pack_chunk(request_id, sequence, chunk))
                            sequence += 1
                            await asyncio.sleep(len(chunk) / (self.device_sample_rate * 2))
                if total_bytes:
                    phrase_id = "dyn-" + sha256(
                        f"{self.synthesizer.voice}\0{self.device_sample_rate}\0{text}".encode("utf-8")
                    ).hexdigest()[:32]
                    async with self._library_write_lock:
                        await asyncio.to_thread(
                            self._cache_generated_phrase, phrase_id, text,
                            self.synthesizer.voice, wav_path, self.device_sample_rate,
                        )
            if not total_bytes:
                raise TtsSynthesisError("Edge TTS 没有产生 PCM 音频")
            if peer.cancel_active.is_set() or peer.closed:
                await self._cancel_phrase(peer, request_id)
                await self._publish_synthesis_status(device_id, task_id, "interrupted")
                return
            await self._send_bytes(peer, pack_control(END, request_id, sequence))
            done_task = asyncio.create_task(peer.playback_done.wait())
            cancel_task = asyncio.create_task(peer.cancel_active.wait())
            try:
                completed, _ = await asyncio.wait(
                    {done_task, cancel_task}, timeout=total_bytes / (self.device_sample_rate * 2) + 3.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not completed:
                    raise TtsSynthesisError("帽端播放完成回执超时")
                if cancel_task in completed and peer.cancel_active.is_set() and not peer.playback_done.is_set():
                    await self._cancel_phrase(peer, request_id)
                    await self._publish_synthesis_status(device_id, task_id, "interrupted")
                    return
            finally:
                for wait_task in (done_task, cancel_task):
                    if not wait_task.done():
                        wait_task.cancel()
                await asyncio.gather(done_task, cancel_task, return_exceptions=True)
            if peer.playback_error is not None:
                raise TtsSynthesisError(f"帽端播放失败，错误码 {peer.playback_error}")
        except asyncio.CancelledError:
            if peer.active_request_id == request_id:
                peer.cancel_active.set()
                if not peer.closed:
                    try:
                        await asyncio.wait_for(asyncio.shield(self._cancel_phrase(peer, request_id)), timeout=1.5)
                    except (OSError, RuntimeError, TtsSynthesisError, asyncio.TimeoutError):
                        pass
            await self._publish_synthesis_status(device_id, task_id, "interrupted")
            raise
        except (TtsSynthesisError, OSError, RuntimeError) as error:
            if peer.active_request_id == request_id and not peer.playback_done.is_set() and not peer.closed:
                try:
                    await asyncio.wait_for(asyncio.shield(self._cancel_phrase(peer, request_id)), timeout=1.5)
                except (OSError, RuntimeError, TtsSynthesisError, asyncio.TimeoutError):
                    pass
            await self._publish_synthesis_status(device_id, task_id, "failed", str(error)[:160])
            if not peer.closed:
                try:
                    await self._send_json(peer, {"type": "tts_delivery_error", "request_id": request_id, "detail": str(error)[:160]})
                except (OSError, RuntimeError):
                    pass
        finally:
            if peer.active_request_id == request_id:
                peer.active_request_id = None
                peer.active_task_id = None
                peer.active_priority = None
                peer.cancel_active.clear()
            peer.active_idle.set()

    async def ensure_phrase(self, text: str) -> TtsPhrase:
        """Return the verified cached WAV for a computer or device output."""
        text = str(text or "").strip()
        if not text:
            raise TtsSynthesisError("语音文本不能为空")
        if len(text) > 500:
            raise TtsSynthesisError("回复太长，单次语音合成上限为 500 字")
        try:
            cached = self.library.get_by_text(text)
        except TtsLibraryError as error:
            raise TtsSynthesisError("本地语音库不可用") from error
        if cached is not None:
            return cached
        if self.synthesizer is None or not getattr(self.synthesizer, "available", True):
            raise TtsSynthesisError("实时合成需要安装 Edge TTS 和 FFmpeg")

        phrase_id = "dyn-" + sha256(
            f"{self.synthesizer.voice}\0{self.synthesizer.sample_rate}\0{text}".encode("utf-8")
        ).hexdigest()[:32]
        with TemporaryDirectory(prefix="qiming-tts-browser-") as directory:
            wav_path = Path(directory) / "reply.wav"
            await self.synthesizer.synthesize_wav(text, wav_path)
            async with self._library_write_lock:
                return await asyncio.to_thread(
                    self._cache_generated_phrase,
                    phrase_id, text, self.synthesizer.voice, wav_path,
                    self.synthesizer.sample_rate,
                )

    async def stream_browser_mp3_and_cache(self, text: str):
        """Relay Edge TTS MP3 immediately and build its PCM cache in parallel."""
        text = str(text or "").strip()
        if not text:
            raise TtsSynthesisError("语音文本不能为空")
        if len(text) > 500:
            raise TtsSynthesisError("回复太长，单次语音合成上限为 500 字")
        if self.synthesizer is None or not getattr(self.synthesizer, "available", True):
            raise TtsSynthesisError("实时合成需要安装 Edge TTS 和 FFmpeg")

        phrase_id = "dyn-" + sha256(
            f"{self.synthesizer.voice}\0{self.synthesizer.sample_rate}\0{text}".encode("utf-8")
        ).hexdigest()[:32]
        with TemporaryDirectory(prefix="qiming-tts-browser-stream-") as directory:
            mp3_path = Path(directory) / "reply.mp3"
            wav_path = Path(directory) / "reply.wav"
            with mp3_path.open("wb") as target:
                async for chunk in self.synthesizer.stream_mp3(text):
                    target.write(chunk)
                    yield chunk
            await self.synthesizer.convert_mp3_to_wav(mp3_path, wav_path)
            async with self._library_write_lock:
                await asyncio.to_thread(
                    self._cache_generated_phrase,
                    phrase_id, text, self.synthesizer.voice, wav_path,
                    self.synthesizer.sample_rate,
                )

    async def _publish_synthesis_status(
        self, device_id: str, task_id: int | None, status: str, detail: str | None = None,
    ) -> None:
        if task_id is None:
            return
        event = {"type": "tts_playback_status", "device_id": device_id,
                 "task_id": task_id, "status": status}
        if detail:
            event["detail"] = detail
        await self.publish_task_status(event)

    @_asset_locked
    def queue_phrase(
        self, device_id: str, phrase_id: str, *, priority: int = 1,
        task_id: int | None = None, ready: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        peer = self.peers.get(device_id)
        if peer is None or peer.closed:
            return {"status": "device_offline"}
        if peer.output_ready is False:
            return {"status": "output_unconfigured"}
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 9:
            return {"status": "invalid_priority"}
        if priority == 0:
            self._preempt_lower_priority(peer)
        try:
            phrase = self.library.get_by_id(phrase_id)
            if phrase.sample_rate != self.device_sample_rate:
                return {"status": "sample_rate_mismatch"}
            request_id = secrets.randbits(32)
            if peer.active_request_id is None:
                peer.cancel_active.clear()
            peer.queue.put_nowait(_QueuedPhrase(
                priority, next(self._orders), request_id, phrase, task_id, peer.generation, ready,
            ))
        except asyncio.QueueFull:
            return {"status": "queue_full"}
        except TtsLibraryError:
            return {"status": "asset_missing"}
        if priority == 0 and peer.active_priority is not None and peer.active_priority > 0:
            peer.cancel_active.set()
        result = {"status": "queued", "phrase_id": phrase.phrase_id}
        if task_id is not None:
            result.update(request_id=request_id, task_id=task_id)
        return result

    def _cache_generated_phrase(self, phrase_id: str, text: str, voice: str, wav_path: Path, sample_rate: int) -> TtsPhrase:
        with self._asset_lock:
            phrase = self.library.get_by_text(text)
            if phrase is not None:
                return phrase
            return self.library.register_generated_wav(
                phrase_id, text, voice, wav_path, sample_rate=sample_rate,
            )

    @_asset_locked
    def _prune_generated_cache(self) -> int:
        pinned_paths = []
        for peer in self.peers.values():
            pinned_paths.extend(item.phrase.path for item in peer.queue._queue)
            if peer.active_phrase is not None:
                pinned_paths.append(peer.active_phrase.path)
        return self.library.prune_generated_cache(pinned_paths=pinned_paths)

    def _preempt_lower_priority(self, peer: TtsPeer) -> None:
        if peer.active_priority is not None and peer.active_priority > 0:
            peer.cancel_active.set()
        for job, (device_id, task_id, priority) in tuple(self._synthesis_task_info.items()):
            if device_id == peer.device_id and priority > 0:
                job.cancel()
        retained = []
        while True:
            try:
                item = peer.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            peer.queue.task_done()
            if item.priority == 0:
                retained.append(item)
            elif item.task_id is not None:
                asyncio.create_task(self._publish_synthesis_status(peer.device_id, item.task_id, "interrupted"))
        for item in retained:
            peer.queue.put_nowait(item)

    def cancel_device(self, device_id: str | None) -> dict[str, Any]:
        peer = self.peers.get(device_id)
        if peer is None or peer.closed:
            return {"status": "device_offline"}
        cancelled = int(peer.active_request_id is not None)
        peer.generation += 1
        self._device_generation[device_id] = self._device_generation.get(device_id, 0) + 1
        for job in tuple(self._synthesis_jobs):
            if job.get_name() == f"tts-synthesis-{device_id}":
                job.cancel()
                cancelled += 1
        if cancelled:
            peer.cancel_active.set()
        while True:
            try:
                item = peer.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            peer.queue.task_done()
            cancelled += 1
            if item.task_id is not None:
                asyncio.create_task(self.publish_task_status({
                    "type": "tts_playback_status", "device_id": device_id,
                    "request_id": item.request_id, "task_id": item.task_id,
                    "status": "interrupted",
                }))
        return {"status": "cancel_requested" if cancelled else "idle", "cancelled": cancelled}

    async def receive(self, peer: TtsPeer, message: str | bytes) -> dict[str, Any] | None:
        if not isinstance(message, bytes):
            return {"type": "tts_error", "code": "CONTROL_MUST_BE_BINARY"}
        try:
            report = unpack_status(message)
        except TtsProtocolError:
            return {"type": "tts_error", "code": "BAD_STATUS_FRAME"}
        peer.last_report = report
        if report["type"] == READY:
            peer.output_ready = report["value"] == 0
            return None
        if report["request_id"] != peer.active_request_id:
            return None
        if report["type"] == PLAYBACK_STARTED:
            peer.playback_started.set()
            status = "speaking"
        elif report["type"] == PLAYBACK_DONE:
            peer.playback_done.set()
            status = "completed"
        elif report["type"] == PLAYBACK_ERROR:
            peer.playback_error = report["value"]
            peer.playback_started.set()
            peer.playback_done.set()
            status = "failed"
        elif report["type"] == PLAYBACK_CANCELLED:
            peer.playback_cancelled.set()
            status = "interrupted"
        else:
            return None
        if peer.active_task_id is None:
            return None
        return {"type": "tts_playback_status", "device_id": peer.device_id,
                "request_id": report["request_id"], "task_id": peer.active_task_id,
                "status": status, "error_code": report.get("value") if status == "failed" else None}

    async def _worker(self, peer: TtsPeer) -> None:
        while not peer.closed:
            item = await peer.queue.get()
            peer.worker_busy = True
            try:
                await peer.active_idle.wait()
                peer.active_idle.clear()
                if item.ready is not None:
                    await item.ready.wait()
                await self._send_phrase(peer, item)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, TtsLibraryError) as error:
                if item.task_id is not None:
                    await self.publish_task_status({
                        "type": "tts_playback_status", "device_id": peer.device_id,
                        "request_id": item.request_id, "task_id": item.task_id,
                        "status": "failed", "detail": str(error)[:160],
                    })
                try:
                    await self._send_json(peer, {
                        "type": "tts_delivery_error",
                        "request_id": item.request_id,
                        "phrase_id": item.phrase.phrase_id,
                        "detail": str(error)[:160],
                    })
                except (OSError, RuntimeError):
                    peer.closed = True
            finally:
                peer.active_request_id = None
                peer.active_task_id = None
                peer.active_priority = None
                peer.active_phrase = None
                peer.cancel_active.clear()
                peer.worker_busy = False
                peer.active_idle.set()
                peer.queue.task_done()
                try:
                    pinned_paths = []
                    for other in self.peers.values():
                        pinned_paths.extend(item.phrase.path for item in other.queue._queue)
                        if other.active_phrase is not None:
                            pinned_paths.append(other.active_phrase.path)
                    await asyncio.to_thread(
                        self.library.prune_generated_cache,
                        pinned_paths=pinned_paths,
                    )
                except (TtsLibraryError, OSError):
                    pass

    async def _send_phrase(self, peer: TtsPeer, item: _QueuedPhrase) -> None:
        phrase = item.phrase
        if item.generation != peer.generation:
            await self._publish_synthesis_status(peer.device_id, item.task_id, "interrupted")
            return
        peer.playback_started.clear()
        peer.playback_done.clear()
        peer.playback_cancelled.clear()
        peer.playback_error = None
        peer.active_request_id = item.request_id
        peer.active_task_id = item.task_id
        peer.active_priority = item.priority
        peer.active_phrase = phrase
        pcm = await asyncio.to_thread(self.library.load_pcm, phrase)
        if peer.cancel_active.is_set() or item.generation != peer.generation:
            await self._cancel_phrase(peer, item.request_id)
            return
        await self._send_bytes(peer, pack_start(item.request_id, phrase.sample_rate, phrase.byte_length))
        started_task = asyncio.create_task(peer.playback_started.wait())
        cancel_task = asyncio.create_task(peer.cancel_active.wait())
        try:
            completed, _pending = await asyncio.wait(
                {started_task, cancel_task}, timeout=3.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not completed:
                raise TtsLibraryError("帽端未确认 TTS 播放准备完成")
            if cancel_task in completed and peer.cancel_active.is_set():
                await self._cancel_phrase(peer, item.request_id)
                await self._publish_synthesis_status(peer.device_id, item.task_id, "interrupted")
                return
        finally:
            for task in (started_task, cancel_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(started_task, cancel_task, return_exceptions=True)
        frame_bytes = max(2, phrase.sample_rate * self.chunk_ms // 1000 * 2)
        sequence = 0
        for offset in range(0, len(pcm), frame_bytes):
            if peer.cancel_active.is_set() or peer.closed:
                await self._cancel_phrase(peer, item.request_id)
                return
            chunk = pcm[offset:offset + frame_bytes]
            await self._send_bytes(peer, pack_chunk(item.request_id, sequence, chunk))
            sequence += 1
            # Pace the stream so the ESP32 ring buffer remains bounded.
            await asyncio.sleep(len(chunk) / (phrase.sample_rate * 2))
        if peer.cancel_active.is_set() or peer.closed:
            await self._cancel_phrase(peer, item.request_id)
            return
        await self._send_bytes(peer, pack_control(END, item.request_id, sequence))
        done_task = asyncio.create_task(peer.playback_done.wait())
        cancel_task = asyncio.create_task(peer.cancel_active.wait())
        try:
            completed, _pending = await asyncio.wait(
                {done_task, cancel_task}, timeout=phrase.duration_ms / 1000 + 3.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not completed:
                raise TtsLibraryError("帽端播放完成回执超时")
            if cancel_task in completed and peer.cancel_active.is_set() and not peer.playback_done.is_set():
                await self._cancel_phrase(peer, item.request_id)
                return
        finally:
            for task in (done_task, cancel_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(done_task, cancel_task, return_exceptions=True)
        if peer.playback_error is not None:
            raise TtsLibraryError(f"帽端播放失败，错误码 {peer.playback_error}")

    async def _cancel_phrase(self, peer: TtsPeer, request_id: int) -> None:
        await self._send_bytes(peer, pack_control(CANCEL, request_id))
        try:
            await asyncio.wait_for(peer.playback_cancelled.wait(), timeout=1.5)
        except asyncio.TimeoutError as error:
            raise TtsLibraryError("帽端取消播放回执超时") from error

    async def _send_bytes(self, peer: TtsPeer, data: bytes) -> None:
        async with peer.send_lock:
            await peer.websocket.send_bytes(data)

    async def _send_json(self, peer: TtsPeer, data: dict[str, Any]) -> None:
        async with peer.send_lock:
            await peer.websocket.send_json(data)


def default_tts_components() -> tuple[TtsLibrary, EdgeTtsSynthesizer]:
    import os
    from pathlib import Path
    from .config import settings

    root = Path(os.getenv("TTS_LIBRARY_DIR", Path(__file__).resolve().parent / "data" / "tts" / "library"))
    synthesizer = EdgeTtsSynthesizer(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        ffmpeg=settings.tts_ffmpeg,
    )
    return TtsLibrary(root), synthesizer


_default_library, _default_synthesizer = default_tts_components()
from .config import settings as _settings

tts_delivery = TtsDelivery(
    _default_library,
    synthesizer=_default_synthesizer,
    device_sample_rate=_settings.tts_sample_rate,
)
