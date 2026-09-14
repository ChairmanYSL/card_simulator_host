"""Event bus: monotonic sequence numbers, ring buffer, fan-out to WS clients."""

from __future__ import annotations

import asyncio
import collections
from typing import Any

MAX_BUFFER = 2000
QUEUE_SIZE = 500  # per-subscriber; drop-oldest when a client lags


class EventBus:
    def __init__(self, max_buffer: int = MAX_BUFFER):
        self._seq = 0
        self._buffer: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=max_buffer)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: str, data: dict[str, Any]) -> int:
        if self._closed:
            return self._seq
        async with self._lock:
            if self._closed:
                return self._seq
            self._seq += 1
            message = {"seq": self._seq, "event": event, "data": data}
            self._buffer.append(message)
            for queue in list(self._subscribers):
                if queue.full():
                    # slow consumer: drop oldest to keep the newest state
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:  # pragma: no cover - defensive
                    pass
            return self._seq

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def events_since(self, seq: int) -> list[dict[str, Any]]:
        async with self._lock:
            return [m for m in self._buffer if m["seq"] > seq]

    async def shutdown(self) -> None:
        """Close the bus and wake every subscriber with a ``None`` sentinel so
        blocked WebSocket loops exit immediately during graceful shutdown."""
        async with self._lock:
            self._closed = True
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:  # pragma: no cover - defensive
                    pass
            self._subscribers.clear()
