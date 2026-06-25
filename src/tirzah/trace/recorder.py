"""Per-request tracer plus event persistence/query.

``Tracer`` is created once per request (anchored on the existing process-run
``run_id`` as the ``trace_id``). Each ``emit`` builds a structured
:class:`~tirzah.trace.events.TraceEvent`, appends it to an in-memory list (so the
request can return ``processEvents`` directly), persists it best-effort to the
``trace_events`` collection, and publishes it to the live bus. Emitting must
never raise into the request path.
"""

from __future__ import annotations

from typing import Any

from tirzah.trace.bus import TraceBus, get_bus
from tirzah.trace.events import (
    INFO,
    TraceEvent,
    new_message_id,
    new_request_id,
    new_trace_id,
)

TRACE_EVENTS_COLLECTION = "trace_events"


def record_event(db: Any, event: TraceEvent) -> None:
    """Best-effort persist of one event to ``trace_events`` (never raises)."""
    if db is None:
        return
    try:
        db[TRACE_EVENTS_COLLECTION].insert_one(event.to_dict())
    except Exception:
        pass


def list_trace_events(
    db: Any,
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Replay events for a trace or session (for the dev-log window initial load)."""
    if db is None:
        return []
    query: dict[str, Any] = {}
    if trace_id:
        query["trace_id"] = trace_id
    if session_id:
        query["session_id"] = session_id
    try:
        rows = list(db[TRACE_EVENTS_COLLECTION].find(query, {"_id": 0}))
    except Exception:
        return []
    rows.sort(key=lambda row: (row.get("timestamp") or "", row.get("seq") or 0))
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def list_trace_sessions(db: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    """Summarise distinct sessions in the trace store (for the log browser).

    One row per ``session_id`` with its sources, event/request counts, time span,
    the first user query, and a preview of the last answer. Aggregates in Python
    over ``find`` so it works against the in-memory test fakes too; fine for now,
    can move to a Mongo aggregation when the log grows.
    """
    if db is None:
        return []
    try:
        rows = list(db[TRACE_EVENTS_COLLECTION].find({}, {"_id": 0}))
    except Exception:
        return []
    sessions: dict[str, dict[str, Any]] = {}
    for event in rows:
        session_id = event.get("session_id")
        if not session_id:
            continue
        summary = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "event_count": 0,
                "_sources": set(),
                "_traces": set(),
                "started_at": None,
                "updated_at": None,
                "first_query": None,
                "last_answer_preview": None,
            },
        )
        summary["event_count"] += 1
        if event.get("source"):
            summary["_sources"].add(event["source"])
        if event.get("trace_id"):
            summary["_traces"].add(event["trace_id"])
        timestamp = event.get("timestamp")
        if timestamp:
            if summary["started_at"] is None or timestamp < summary["started_at"]:
                summary["started_at"] = timestamp
            if summary["updated_at"] is None or timestamp > summary["updated_at"]:
                summary["updated_at"] = timestamp
        metadata = event.get("metadata") or {}
        if event.get("type") == "message.user.submitted" and summary["first_query"] is None:
            summary["first_query"] = metadata.get("query")
        if event.get("type") == "answer.finalized":
            answer = metadata.get("answer")
            if isinstance(answer, str):
                summary["last_answer_preview"] = answer[:160]
    result = []
    for summary in sessions.values():
        result.append(
            {
                "session_id": summary["session_id"],
                "event_count": summary["event_count"],
                "sources": sorted(summary["_sources"]),
                "trace_count": len(summary["_traces"]),
                "started_at": summary["started_at"],
                "updated_at": summary["updated_at"],
                "first_query": summary["first_query"],
                "last_answer_preview": summary["last_answer_preview"],
            }
        )
    result.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return result[:limit]


class Tracer:
    """Emits structured trace events for a single request lifecycle."""

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        session_id: str,
        db: Any = None,
        bus: TraceBus | None = None,
        source: str = "tirzah",
        message_id: str | None = None,
        request_id: str | None = None,
        persist: bool = True,
    ) -> None:
        self.trace_id = trace_id or new_trace_id()
        self.session_id = session_id
        self.source = source
        self.message_id = message_id
        self.request_id = request_id or new_request_id()
        self._db = db
        self._bus = bus if bus is not None else get_bus()
        self._persist = persist
        self._seq = 0
        self.events: list[TraceEvent] = []

    def emit(
        self,
        type: str,
        *,
        status: str = "ok",
        summary: str = "",
        severity: str = INFO,
        message_id: str | None = None,
        **metadata: Any,
    ) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(
            trace_id=self.trace_id,
            session_id=self.session_id,
            type=type,
            status=status,
            summary=summary,
            severity=severity,
            source=self.source,
            message_id=message_id or self.message_id,
            request_id=self.request_id,
            seq=self._seq,
            metadata=dict(metadata),
        )
        self.events.append(event)
        if self._persist:
            record_event(self._db, event)
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                pass
        return event

    # Convenience wrappers for the common status triplet.
    def started(self, type: str, summary: str = "", **metadata: Any) -> TraceEvent:
        return self.emit(type, status="started", summary=summary, **metadata)

    def completed(self, type: str, summary: str = "", **metadata: Any) -> TraceEvent:
        return self.emit(type, status="completed", summary=summary, **metadata)

    def failed(self, type: str, summary: str = "", **metadata: Any) -> TraceEvent:
        return self.emit(type, status="failed", severity="error", summary=summary, **metadata)

    def new_message_id(self) -> str:
        self.message_id = new_message_id()
        return self.message_id

    def as_dicts(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]
