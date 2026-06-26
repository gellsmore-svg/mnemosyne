"""Tirzah's background memory work runs on Hoglah's general priority queue.

The scheduling primitive (priority + per-key serial execution) is a general Hoglah
feature (``hoglah.SessionPriorityQueue``), reusable in any context. Here we only
define the memory-task priority ladder and a process-wide queue keyed by
session_id, so a turn's embedding completes before its chunking and one session's
backlog never blocks another's. The durable Mongo backfill remains the restart-safe
catch-up.
"""

from __future__ import annotations

import threading

from hoglah import SessionPriorityQueue

# Memory-task priorities on Hoglah's ladder (lower = higher priority), per the spec.
PRIORITY_MEMORY_COMPLETION = 2  # turn embedding (semantic recall completion)
PRIORITY_CHUNKING = 3
PRIORITY_RELATIONSHIPS = 4
PRIORITY_ENRICHMENT = 5
PRIORITY_MAINTENANCE = 6

_QUEUE: SessionPriorityQueue | None = None
_LOCK = threading.Lock()


def get_background_queue() -> SessionPriorityQueue:
    """Process-wide background queue for memory work, keyed by session_id."""
    global _QUEUE
    if _QUEUE is None:
        with _LOCK:
            if _QUEUE is None:
                _QUEUE = SessionPriorityQueue(workers=2)
    return _QUEUE
