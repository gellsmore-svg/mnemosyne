"""Bridge the answer pipeline's existing ``process_trace`` to structured trace events.

This is the *Tirzah-specific* adapter that re-expresses the rich-but-ad-hoc
``process_trace`` (a list of ``{step, input, output}`` dicts) as the structured,
extensible event stream defined in :mod:`galeed`. Keeping it here leaves the
``galeed`` spine free of Tirzah imports so it can be extracted to a shared
library later.

Design rule: events carry **lean, whitelisted** metadata only — never the prompt
text, context payloads, or other process scaffolding. That heavy material is the
thing we are separating out of the answer; it stays in the legacy activity fields
and can be surfaced in the dev-log on demand, not bloated into every event.
"""

from __future__ import annotations

from typing import Any

from tirzah.sessions.activity_reports import human_step_name
from galeed.events import EventType
from galeed.recorder import Tracer

# process_trace step name -> canonical structured event type.
_STEP_EVENT_TYPE: dict[str, str] = {
    "user_prompt": EventType.MESSAGE_USER_SUBMITTED,
    "retrieval_context": EventType.CONTEXT_SELECTED,
    "search": EventType.RETRIEVAL_MONGO_COMPLETED,
    "memory_agent_iteration": EventType.RETRIEVAL_MONGO_COMPLETED,
    "deep_retrieval": EventType.RETRIEVAL_MONGO_COMPLETED,
    "answer_adapter": EventType.MODEL_RESPONSE_COMPLETED,
    "save_exchange": EventType.LOG_PERSISTED,
    "stop": EventType.PROCESS_COMPLETED,
    "sufficiency": "context.sufficiency",
    "specialist_coherence": EventType.SPECIALIST_COMPLETED,
    "request_plan": EventType.PROCESS_STEP,
    "plan_execution": EventType.PROCESS_STEP,
    "plan.revision.proposed": EventType.PROCESS_STEP,
    "plan.revision.executed": EventType.PROCESS_STEP,
    "plan.parallel.branch": EventType.PROCESS_STEP,
    "plan.parallel.completed": EventType.PROCESS_STEP,
    "plan.parallel.merged": EventType.PROCESS_STEP,
    "plan.retry.attempt": EventType.PROCESS_STEP,
    "context_bundle": EventType.CONTEXT_SELECTED,
    "plan.iterate.round": EventType.PROCESS_STEP,
    "plan.decision.selected": EventType.PROCESS_STEP,
    "plan.step.started": EventType.PROCESS_STEP,
    "plan.step.completed": EventType.PROCESS_STEP,
    "plan.step.blocked": EventType.PROCESS_STEP,
    "plan.step.skipped": EventType.PROCESS_STEP,
    "plan.loop.break": EventType.PROCESS_STEP,
    "plan.loop.continue": EventType.PROCESS_STEP,
}

# small, safe scalar/identifier fields worth surfacing as event metadata.
_META_WHITELIST = (
    "adapter",
    "model",
    "retrieval_mode",
    "retrieval_status",
    "retrieval_decision",
    "ok",
    "tool",
    "plan_id",
    "status",
    "node_count",
    "count",
    "iteration",
    "stop_reason",
    "context_sufficiency_score",
    "recursion",
    "remaining_uncertainty_count",
    "mode",
    "claims",
    "objections",
    "confidence",
    "terminal_reason",
    "branch",
    "round",
    "max_rounds",
    "step_id",
    "construct",
    "reason",
    "revision",
    "parent_revision",
    "revision_decision",
    "revision_reason",
    "step_count",
    "signal",
    "selected_steps",
    "body",
    "condition",
    "skipped_parent",
    "loop_break",
    "inline_decision_branch",
    "inline_parallel_branch",
    "decision_id",
    "parallel_id",
    "parallel_state",
    "merge",
    "attempt",
    "max_attempts",
    "retry_attempt",
    "tool_count",
    "completed_count",
    "step_count",
    "artifact_keys",
    "tools",
)

_MAX_STR = 200


def _compact_meta(step: dict[str, Any]) -> dict[str, Any]:
    """Pull a bounded, whitelisted set of small fields from a trace step."""
    meta: dict[str, Any] = {}
    if step.get("step_id") is not None:
        meta["step_id"] = step["step_id"]
    sources: list[Any] = [step.get("input"), step.get("output"), step.get("metadata")]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _META_WHITELIST:
            if key in source and key not in meta:
                value = source[key]
                if isinstance(value, str) and len(value) > _MAX_STR:
                    value = value[:_MAX_STR] + "…"
                if isinstance(value, (str, int, float, bool)) or value is None:
                    meta[key] = value
                elif isinstance(value, list) and key == "selected_steps":
                    meta["selected_steps"] = [str(item) for item in value[:12]]
                elif isinstance(value, list) and key == "body":
                    meta["body"] = [str(item) for item in value[:12]]
        # derive a count of included/returned items without dumping them
        for list_key in ("included_nodes", "tool_results", "results", "candidates"):
            items = source.get(list_key)
            if isinstance(items, list) and "result_count" not in meta:
                meta["result_count"] = len(items)
    return meta


def _status_for(step: dict[str, Any], name: str | None) -> str:
    output = step.get("output")
    if name == "invalid_plan":
        return "failed"
    if isinstance(output, dict) and output.get("ok") is False:
        return "failed"
    return "ok"


def emit_process_trace_events(
    tracer: Tracer,
    process_trace: list[dict[str, Any]] | None,
    *,
    skip_steps: tuple[str, ...] = ("user_prompt",),
) -> None:
    """Emit one structured event per process_trace step through ``tracer``.

    ``user_prompt`` is skipped by default because the web boundary emits
    ``message.user.submitted`` itself (with the live trace id) before the pipeline
    runs.
    """
    for step in process_trace or []:
        name = step.get("step")
        if name in skip_steps:
            continue
        event_type = _STEP_EVENT_TYPE.get(name or "", "process.step")
        summary = human_step_name(name) or (name or "step")
        status = _status_for(step, name)
        severity = "error" if status == "failed" else "info"
        metadata = _compact_meta(step)
        # A trace step's own fields (e.g. output.status) must not collide with
        # emit()'s reserved keyword arguments; namespace any that would.
        for reserved in ("type", "status", "summary", "severity", "message_id", "step"):
            if reserved in metadata:
                metadata[f"step_{reserved}"] = metadata.pop(reserved)
        tracer.emit(
            event_type,
            status=status,
            summary=summary,
            severity=severity,
            step=name,
            **metadata,
        )


def record_llm_calls_from_trace(
    db: Any,
    tracer: Tracer,
    process_trace: list[dict[str, Any]] | None,
) -> None:
    """Record full In→Out documents (galeed ``llm_calls``) for the model calls
    in a ``process_trace`` — Tirzah's entry in the family LLM debugging view.

    The events emitted above stay lean by design; the ``llm_calls`` collection
    is the sanctioned home for the heavy material (full prompt, full answer),
    which `galeed trace` and Mizpah's LLM Calls tab read. One document per
    ``answer_adapter`` step, correlated by the live trace/session ids.
    Best-effort: recording never raises into the request path.
    """
    from galeed import record_llm_call

    for step in process_trace or []:
        if step.get("step") != "answer_adapter":
            continue
        step_in = step.get("input") if isinstance(step.get("input"), dict) else {}
        step_out = step.get("output") if isinstance(step.get("output"), dict) else {}
        failed = step_out.get("ok") is False
        record_llm_call(
            db,
            trace_id=tracer.trace_id,
            session_id=tracer.session_id,
            source=tracer.source,
            step_name="answer",
            model=step_out.get("model") or step_in.get("model"),
            prompt=step_in.get("prompt_text"),
            output=None if failed else step_out.get("answer"),
            error=str(step_out.get("error")) if failed else None,
            metadata={
                "adapter": step_out.get("adapter") or step_in.get("adapter"),
                "used_node_count": len(step_out.get("used_node_ids") or []),
                "request_id": tracer.request_id,
            },
            emit_event=False,  # model.response.completed already marks the spine
        )
