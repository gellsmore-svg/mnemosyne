"""One traced interaction entrypoint shared by every surface.

Single mechanism for all interaction types — web ``/api/ask``, CLI ``ask`` and
``chat``, and any future route — so the structured trace-event stream and the
3-channel answer / process / log separation are produced **uniformly**, not
re-implemented per entrypoint. Callers pass their normal answer arguments; this
function owns the :class:`~galeed.recorder.Tracer` lifecycle, translates the
pipeline's ``process_trace`` into structured events, and attaches the contract
fields (``processEvents``, ``traceId``, ``sessionId``, ``messageId``,
``requestId``) to the returned result.
"""

from __future__ import annotations

from typing import Any, Callable

from tirzah.config import AppConfig
from tirzah.planning.recursive import process_frontend_request
from tirzah.sessions.interaction import answer_query
from tirzah.sessions.process_events import (
    emit_process_trace_events,
    record_llm_calls_from_trace,
)
from galeed import EventType, Tracer


def run_traced_interaction(
    db: Any,
    config: AppConfig,
    *,
    query: str,
    session_id: str = "web",
    executor: Callable[..., dict[str, Any]] = answer_query,
    planning_enabled: bool | None = None,
    tracer: Tracer | None = None,
    source: str = "tirzah",
    **answer_kwargs: Any,
) -> dict[str, Any]:
    tracer = tracer or Tracer(session_id=session_id, db=db, source=source)
    tracer.new_message_id()
    # Live bookends, emitted before the (synchronous) pipeline so live subscribers
    # (SSE process panel / dev-log window) see the request begin immediately.
    tracer.emit(
        EventType.MESSAGE_USER_SUBMITTED,
        summary="User submitted a question",
        query=query,
        retrieval_mode=answer_kwargs.get("retrieval_mode"),
    )
    tracer.started(EventType.PROCESS_STARTED, "Processing request")
    try:
        result = process_frontend_request(
            db,
            config,
            query=query,
            executor=executor,
            planning_enabled=planning_enabled,
            session_id=session_id,
            tracer=tracer,  # plan-step events stream live to the process panel
            **answer_kwargs,
        )
    except Exception as error:  # noqa: BLE001 - log a failure event, then re-raise
        tracer.failed(EventType.PROCESS_FAILED, f"Request failed: {error}")
        raise
    # Re-express the pipeline's existing process_trace as structured events.
    emit_process_trace_events(tracer, result.get("process_trace"))
    record_llm_calls_from_trace(db, tracer, result.get("process_trace"))
    tracer.completed(
        EventType.ANSWER_FINALIZED,
        "Final answer ready",
        answer=result.get("answer") or "",
        adapter=result.get("answer_adapter"),
        model=result.get("answer_model"),
    )
    tracer.completed(EventType.PROCESS_COMPLETED, "Request complete")
    result["processEvents"] = tracer.as_dicts()
    result["traceId"] = tracer.trace_id
    result["sessionId"] = result.get("session_id") or session_id
    result["messageId"] = tracer.message_id
    result["requestId"] = tracer.request_id
    return result
