"""In-process pub/sub for live trace streaming (SSE / live dev-log window).

A single process-wide :class:`TraceBus` lets request handlers publish
:class:`~tirzah.trace.events.TraceEvent` objects while SSE subscribers (the live
process panel and the separate dev-log window) receive them in near-real-time,
filtered by ``trace_id`` or ``session_id``. Best-effort and non-blocking:
publishing must never slow down or break a request.
"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Iterator

from tirzah.trace.events import TraceEvent

WILDCARD = "*"


class TraceBus:
    def __init__(self, *, max_queue: int = 1000) -> None:
        self._max_queue = max_queue
        self._lock = threading.Lock()
        # channel -> list of subscriber queues. Channels are trace_id, session_id,
        # and WILDCARD (everything).
        self._subscribers: dict[str, list["queue.Queue[TraceEvent]"]] = {}

    def publish(self, event: TraceEvent) -> None:
        channels = {event.trace_id, event.session_id, WILDCARD}
        with self._lock:
            targets = [q for ch in channels for q in self._subscribers.get(ch, [])]
        # de-dup queues (a subscriber may match multiple channels) and deliver
        for sub in {id(q): q for q in targets}.values():
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass  # slow consumer: drop rather than block the request

    def _add(self, channel: str, sub: "queue.Queue[TraceEvent]") -> None:
        with self._lock:
            self._subscribers.setdefault(channel, []).append(sub)

    def _remove(self, channel: str, sub: "queue.Queue[TraceEvent]") -> None:
        with self._lock:
            subs = self._subscribers.get(channel)
            if subs and sub in subs:
                subs.remove(sub)
                if not subs:
                    self._subscribers.pop(channel, None)

    @contextmanager
    def subscribe(self, channel: str = WILDCARD) -> Iterator["queue.Queue[TraceEvent]"]:
        """Subscribe to a channel (a trace_id, a session_id, or WILDCARD)."""
        sub: "queue.Queue[TraceEvent]" = queue.Queue(maxsize=self._max_queue)
        self._add(channel, sub)
        try:
            yield sub
        finally:
            self._remove(channel, sub)

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(subs) for subs in self._subscribers.values())


_BUS: TraceBus | None = None
_BUS_LOCK = threading.Lock()


def get_bus() -> TraceBus:
    """Process-wide singleton bus shared by the app and request handlers."""
    global _BUS
    if _BUS is None:
        with _BUS_LOCK:
            if _BUS is None:
                _BUS = TraceBus()
    return _BUS
