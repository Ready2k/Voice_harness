"""Application inbound queue with a seconds-of-audio bound (spec section 13, WS-02).

A reader task drains the socket into this queue so a slow consumer is observable.
When queued audio exceeds the limit the queue is marked overrun; the connection
handler stops the run with AUDIO_BACKPRESSURE instead of silently dropping or
buffering without bound.
"""

from __future__ import annotations

import asyncio
import time

AUDIO_FRAME_SECONDS = 0.02
MAX_QUEUED_MESSAGES = 1000


class InboundQueue:
    def __init__(self, limit_seconds=2.0, max_messages=MAX_QUEUED_MESSAGES):
        self.limit_seconds, self.max_messages = limit_seconds, max_messages
        self.items: asyncio.Queue = asyncio.Queue()
        self.queued_seconds = 0.0
        self.high_water_seconds = 0.0
        self.overrun = False
        self.last_received = None  # monotonic seconds when the last message was read
        self.task: asyncio.Task | None = None

    @staticmethod
    def weight(message):
        if isinstance(message, dict) and message.get("type") == "audio.input":
            return AUDIO_FRAME_SECONDS
        return 0.0

    def offer(self, message, received=None):
        """Enqueue unless the bound is exceeded; returns False once overrun."""
        if self.overrun:
            return False
        weight = self.weight(message)
        if (
            self.queued_seconds + weight > self.limit_seconds + 1e-9
            or self.items.qsize() >= self.max_messages
        ):
            self.overrun = True
            return False
        self.queued_seconds += weight
        self.high_water_seconds = max(self.high_water_seconds, self.queued_seconds)
        self.items.put_nowait(("message", message, weight, received))
        return True

    def start(self, socket):
        self.task = asyncio.create_task(self._read(socket))

    async def _read(self, socket):
        try:
            while True:
                message = await socket.receive_json()
                if not self.offer(message, time.monotonic()):
                    return
        except Exception as error:  # noqa: BLE001 -- surfaced to the consumer in order
            self.items.put_nowait(("error", error, 0.0, None))

    async def get(self, timeout):
        """Next message, raising queued transport errors; TimeoutError when idle."""
        if self.overrun:
            raise ValueError("AUDIO_BACKPRESSURE")
        kind, value, weight, received = await asyncio.wait_for(
            self.items.get(), timeout
        )
        self.last_received = received
        self.queued_seconds = max(0.0, self.queued_seconds - weight)
        if kind == "error":
            raise value
        return value

    def close(self):
        if self.task:
            self.task.cancel()
