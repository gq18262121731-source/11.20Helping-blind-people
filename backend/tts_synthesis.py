"""Optional Edge TTS synthesis for replies not present in the local phrase cache."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import shutil


class TtsSynthesisError(RuntimeError):
    """Edge TTS or FFmpeg could not produce the configured PCM WAV output."""


class EdgeTtsSynthesizer:
    def __init__(self, *, voice: str, sample_rate: int, ffmpeg: str = "ffmpeg"):
        self.voice = voice
        self.sample_rate = sample_rate
        self.ffmpeg = ffmpeg
        self._semaphore = asyncio.Semaphore(2)

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("edge_tts") is not None and (
            shutil.which(self.ffmpeg) is not None or Path(self.ffmpeg).is_file()
        )

    async def synthesize_wav(self, text: str, output_path: Path) -> None:
        if len(text) > 500:
            raise TtsSynthesisError("回复太长，单次语音合成上限为 500 字")
        if shutil.which(self.ffmpeg) is None and not Path(self.ffmpeg).is_file():
            raise TtsSynthesisError("未找到 FFmpeg，请安装后再启用实时语音合成")
        try:
            import edge_tts
        except ImportError as error:
            raise TtsSynthesisError("未安装 Edge TTS，请安装 backend/requirements-tts.txt") from error

        async with self._semaphore:
            source = output_path.with_suffix(".mp3")
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(text=text, voice=self.voice).save(str(source)),
                    timeout=90,
                )
                process = await asyncio.create_subprocess_exec(
                    self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-map_metadata", "-1", "-ac", "1",
                    "-ar", str(self.sample_rate), "-c:a", "pcm_s16le", str(output_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise TtsSynthesisError("FFmpeg 转码超时") from None
                if process.returncode != 0:
                    detail = (stderr or b"").decode("utf-8", errors="replace")[-240:]
                    raise TtsSynthesisError("FFmpeg 转码失败" + (f"：{detail}" if detail else ""))
            except asyncio.TimeoutError:
                raise TtsSynthesisError("Edge TTS 合成超时") from None
            except TtsSynthesisError:
                raise
            except Exception as error:
                raise TtsSynthesisError(f"Edge TTS 合成失败：{type(error).__name__}") from error
            finally:
                source.unlink(missing_ok=True)

    async def stream_mp3(self, text: str):
        """Yield Edge TTS MP3 frames as they arrive from the service."""
        if len(text) > 500:
            raise TtsSynthesisError("回复太长，单次语音合成上限为 500 字")
        try:
            import edge_tts
        except ImportError as error:
            raise TtsSynthesisError("未安装 Edge TTS，请安装 backend/requirements-tts.txt") from error
        async with self._semaphore:
            try:
                async for message in edge_tts.Communicate(text=text, voice=self.voice).stream():
                    if message.get("type") == "audio" and message.get("data"):
                        yield message["data"]
            except Exception as error:
                raise TtsSynthesisError(f"Edge TTS 流式合成失败：{type(error).__name__}") from error

    async def convert_mp3_to_wav(self, source: Path, output_path: Path) -> None:
        """Convert a streamed MP3 file to the configured mono PCM WAV format."""
        if shutil.which(self.ffmpeg) is None and not Path(self.ffmpeg).is_file():
            raise TtsSynthesisError("未找到 FFmpeg，请安装后再启用实时语音合成")
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn", "-map_metadata", "-1", "-ac", "1",
            "-ar", str(self.sample_rate), "-c:a", "pcm_s16le", str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TtsSynthesisError("FFmpeg 转码超时") from None
        if process.returncode != 0:
            detail = (stderr or b"").decode("utf-8", errors="replace")[-240:]
            raise TtsSynthesisError("FFmpeg 转码失败" + (f"：{detail}" if detail else ""))

    async def stream_pcm(self, text: str, *, output_rate: int | None = None):
        """Yield PCM as Edge TTS audio arrives, using FFmpeg pipes without temp MP3."""
        if len(text) > 500:
            raise TtsSynthesisError("回复太长，单次语音合成上限为 500 字")
        if shutil.which(self.ffmpeg) is None and not Path(self.ffmpeg).is_file():
            raise TtsSynthesisError("未找到 FFmpeg，请安装后再启用实时语音合成")
        try:
            import edge_tts
        except ImportError as error:
            raise TtsSynthesisError("未安装 Edge TTS，请安装 backend/requirements-tts.txt") from error

        sample_rate = int(output_rate or self.sample_rate)
        if not 8_000 <= sample_rate <= 48_000:
            raise TtsSynthesisError("PCM 采样率无效")
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
            "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = edge_tts.Communicate(text=text, voice=self.voice)
        stderr_task = asyncio.create_task(process.stderr.read())
        feed_error: list[BaseException] = []

        class _StreamFailure(Exception):
            def __init__(self, original: BaseException):
                self.original = original

        async def feed_edge_audio() -> None:
            try:
                async with self._semaphore:
                    async for message in communicate.stream():
                        if message.get("type") != "audio":
                            continue
                        data = message.get("data") or b""
                        if not data:
                            continue
                        process.stdin.write(data)
                        await process.stdin.drain()
            except BaseException as error:
                feed_error.append(error)
                raise _StreamFailure(error) from error
            finally:
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        feed_task = asyncio.create_task(feed_edge_audio())
        try:
            if process.stdout is None:
                raise TtsSynthesisError("FFmpeg PCM 输出管道不可用")
            while True:
                chunk = await process.stdout.read(2_560)
                if not chunk:
                    break
                if len(chunk) & 1:
                    raise TtsSynthesisError("FFmpeg 返回未对齐的 PCM 样本")
                yield chunk
            await asyncio.wait_for(feed_task, timeout=90)
            return_code = await asyncio.wait_for(process.wait(), timeout=45)
            stderr = await stderr_task
            if return_code != 0:
                detail = (stderr or b"").decode("utf-8", errors="replace")[-240:]
                raise TtsSynthesisError("FFmpeg 流式解码失败" + (f"：{detail}" if detail else ""))
            if feed_error:
                error = feed_error[0]
                raise TtsSynthesisError(f"Edge TTS 流式合成失败：{type(error).__name__}") from error
        except asyncio.TimeoutError as error:
            process.kill()
            await process.wait()
            raise TtsSynthesisError("Edge TTS 流式合成或 FFmpeg 转码超时") from error
        except asyncio.CancelledError:
            feed_task.cancel()
            process.kill()
            await process.wait()
            await asyncio.gather(feed_task, return_exceptions=True)
            raise
        except _StreamFailure as error:
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(feed_task, return_exceptions=True)
            raise TtsSynthesisError(f"Edge TTS 流式合成失败：{type(error.original).__name__}") from error.original
        except TtsSynthesisError:
            feed_task.cancel()
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(feed_task, return_exceptions=True)
            raise
        except Exception as error:
            feed_task.cancel()
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(feed_task, return_exceptions=True)
            raise TtsSynthesisError(f"Edge TTS 流式处理失败：{type(error).__name__}") from error
