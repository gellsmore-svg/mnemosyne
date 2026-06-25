"""Structured feedback records attached to a trace/session.

Feedback (from a human or another AI assistant) is a first-class, cross-project
concept: a free-text observation tied to the current ``trace_id`` / ``session_id``
so it can later be triaged, analysed, or converted into a GitHub issue. Stored in
its own ``feedback`` collection *and* mirrored as a ``feedback.submitted`` trace
event by the caller, so it shows up in the log stream too. Best-effort: persistence
never raises into the request path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

FEEDBACK_COLLECTION = "feedback"


def new_feedback_id() -> str:
    return f"fb_{uuid4().hex}"


def record_feedback(
    db: Any,
    *,
    text: str,
    session_id: str,
    trace_id: str | None = None,
    message_id: str | None = None,
    source: str = "user",
    kind: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one structured feedback record (best-effort) and return it."""
    record = {
        "feedback_id": new_feedback_id(),
        "text": text,
        "session_id": session_id,
        "trace_id": trace_id,
        "message_id": message_id,
        "source": source,
        "kind": kind,
        "context": context or {},
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if db is not None:
        try:
            db[FEEDBACK_COLLECTION].insert_one(dict(record))
        except Exception:
            pass
    return record


def list_feedback(
    db: Any,
    *,
    session_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    query: dict[str, Any] = {}
    if session_id:
        query["session_id"] = session_id
    if trace_id:
        query["trace_id"] = trace_id
    try:
        rows = list(db[FEEDBACK_COLLECTION].find(query, {"_id": 0}))
    except Exception:
        return []
    rows.sort(key=lambda row: row.get("created_at") or "")
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows
