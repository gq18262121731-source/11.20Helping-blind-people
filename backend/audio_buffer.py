"""Bounded PCM ring buffer used by file and WebSocket audio sources."""
from __future__ import annotations

from collections import deque


class AudioBuffer:
    def __init__(self, max_bytes: int = 16_000 * 2 * 12):
        if max_bytes < 2:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes - max_bytes % 2
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, data: bytes) -> None:
        if len(data) % 2:
            raise ValueError("PCM chunk must contain whole samples")
        if len(data) >= self.max_bytes:
            data = data[-self.max_bytes:]
            self._chunks.clear(); self._size = 0
        self._chunks.append(bytes(data)); self._size += len(data)
        while self._size > self.max_bytes:
            overflow = self._size - self.max_bytes
            chunk = self._chunks.popleft()
            if len(chunk) <= overflow:
                self._size -= len(chunk)
            else:
                self._chunks.appendleft(chunk[overflow:])
                self._size -= overflow

    def read(self, clear: bool = False) -> bytes:
        value = b"".join(self._chunks)
        if clear:
            self.clear()
        return value

    def clear(self) -> None:
        self._chunks.clear(); self._size = 0

    def __len__(self):
        return self._size
