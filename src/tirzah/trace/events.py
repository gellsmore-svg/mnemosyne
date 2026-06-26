"""Structured trace-event vocabulary and identifiers.

This is the cross-project trace/log spine. It lives in Tirzah for now but is
deliberately self-contained (no Tirzah-specific imports) so it can be extracted
into a shared library that MAHALATH / HOGLAH / CAIRN / MILKA also emit into.

The event vocabulary below is the *documented* Tirzah set. It is intentionally
NOT a closed enum: `Tracer.emit` accepts any ``type`` string, so other sources
(and future CAIRN-governed processes) can extend it without code changes here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

# --- severity levels -------------------------------------------------------
DEBUG = "debug"
INFO = "info"
WARNING = "warning"
ERROR = "error"


class EventType:
    """Documented Tirzah event vocabulary (extensible; see module docstring)."""

    # session / message lifecycle
    SESSION_CREATED = "session.created"
    SESSION_SELECTED = "session.selected"
    MESSAGE_USER_SUBMITTED = "message.user.submitted"
    # request lifecycle
    PROCESS_STARTED = "process.started"
    PROCESS_COMPLETED = "process.completed"
    PROCESS_FAILED = "process.failed"
    # retrieval
    RETRIEVAL_MONGO_STARTED = "retrieval.mongo.started"
    RETRIEVAL_MONGO_COMPLETED = "retrieval.mongo.completed"
    RETRIEVAL_MONGO_FAILED = "retrieval.mongo.failed"
    CONTEXT_SELECTED = "context.selected"
    CONTEXT_SUFFICIENCY = "context.sufficiency"
    # external research
    RESEARCH_CONSIDERED = "research.considered"
    RESEARCH_STARTED = "research.started"
    RESEARCH_COMPLETED = "research.completed"
    RESEARCH_FAILED = "research.failed"
    # model / answer
    MODEL_PROMPT_BUILT = "model.prompt.built"
    MODEL_RESPONSE_STARTED = "model.response.started"
    MODEL_RESPONSE_COMPLETED = "model.response.completed"
    ANSWER_FINALIZED = "answer.finalized"
    # persistence / feedback
    LOG_PERSISTED = "log.persisted"
    MEMORY_UPDATE_CONSIDERED = "memory.update.considered"
    MEMORY_UPDATE_COMPLETED = "memory.update.completed"
    FEEDBACK_SUBMITTED = "feedback.submitted"


KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    value for key, value in vars(EventType).items() if not key.startswith("_") and isinstance(value, str)
)


# --- stable identifiers ----------------------------------------------------
def new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def new_message_id() -> str:
    return f"msg_{uuid4().hex}"


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TraceEvent:
    """One structured event in a request's lifecycle.

    ``source`` defaults to ``"tirzah"`` but is a parameter so the same stream can
    carry events from other family projects. ``type`` is a free string drawn from
    :class:`EventType` by convention; ``metadata`` carries structured detail.
    """

    trace_id: str
    session_id: str
    type: str
    status: str = "ok"
    summary: str = ""
    severity: str = INFO
    source: str = "tirzah"
    message_id: str | None = None
    request_id: str | None = None
    seq: int = 0
    event_id: str = field(default_factory=new_event_id)
    timestamp: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form (ISO timestamp) for API responses, SSE, and storage."""
        data = asdict(self)
        if isinstance(self.timestamp, datetime):
            data["timestamp"] = self.timestamp.isoformat()
        return data
