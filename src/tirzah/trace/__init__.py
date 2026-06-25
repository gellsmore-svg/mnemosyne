"""Tirzah trace/log spine — structured cross-project event stream.

Separates *process telemetry* (structured events) from the *final answer*. The
backend emits events through :class:`~tirzah.trace.recorder.Tracer`; the API
returns them as ``processEvents``; the live bus streams them to the process panel
and the separate dev-log window. Designed to be extracted into a shared library
that other family projects (MAHALATH, HOGLAH, CAIRN, MILKA) can emit into.
"""

from tirzah.trace.bus import TraceBus, get_bus
from tirzah.trace.events import (
    KNOWN_EVENT_TYPES,
    EventType,
    TraceEvent,
    new_event_id,
    new_message_id,
    new_request_id,
    new_trace_id,
)
from tirzah.trace.feedback import (
    FEEDBACK_COLLECTION,
    list_feedback,
    record_feedback,
)
from tirzah.trace.recorder import (
    TRACE_EVENTS_COLLECTION,
    Tracer,
    list_trace_events,
    record_event,
)

__all__ = [
    "EventType",
    "KNOWN_EVENT_TYPES",
    "TraceEvent",
    "TraceBus",
    "Tracer",
    "TRACE_EVENTS_COLLECTION",
    "FEEDBACK_COLLECTION",
    "get_bus",
    "list_trace_events",
    "record_event",
    "record_feedback",
    "list_feedback",
    "new_event_id",
    "new_message_id",
    "new_request_id",
    "new_trace_id",
]
