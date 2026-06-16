from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database


ANSWER_PREVIEW_CHARS = 600
TRACE_SUMMARY_LIMIT = 12
SKIPPED_SUMMARY_LIMIT = 12


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def persist_prompt_iteration(
    db: Database,
    *,
    exchange_id: str,
    session_id: str,
    project_domain_id: str,
    conversation_domain_id: str,
    query: str,
    answer: dict[str, Any],
    prompt: dict[str, Any],
    focus_node_id: str | None,
    used_node_ids: list[str],
    active_document_ids: list[str],
    process_trace: list[dict[str, Any]],
) -> str | None:
    if not hasattr(db, "session_continuity"):
        return None
    now = utc_now()
    db.session_continuity.update_many(
        {"session_id": session_id, "is_latest": True},
        {"$set": {"is_latest": False, "superseded_at": now}},
    )
    context_metadata = prompt.get("context_metadata") or {}
    result = db.session_continuity.insert_one(
        {
            "schema_version": 1,
            "session_id": session_id,
            "project_domain_id": project_domain_id,
            "conversation_domain_id": conversation_domain_id,
            "exchange_id": object_id_or_string(exchange_id),
            "query": query,
            "focus_node_id": object_id_or_string(focus_node_id),
            "used_node_ids": [object_id_or_string(node_id) for node_id in used_node_ids],
            "active_document_ids": [
                object_id_or_string(document_id) for document_id in active_document_ids
            ],
            "prompt_budget": prompt.get("budget", {}),
            "context_metadata": context_metadata,
            "controller_decision": context_metadata.get("controller_decision"),
            "evidence_summary": context_metadata.get("evidence_summary"),
            "answer_preview": answer_preview(answer.get("answer")),
            "answer_adapter": answer.get("adapter"),
            "answer_model": answer.get("model"),
            "process_trace_summary": process_trace_summary(process_trace),
            "skipped_summary": skipped_summary(context_metadata.get("skipped") or []),
            "is_latest": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


def latest_prompt_iteration(db: Database, session_id: str) -> dict[str, Any] | None:
    if not hasattr(db, "session_continuity"):
        return None
    row = db.session_continuity.find_one(
        {"session_id": session_id, "is_latest": True},
        sort=[("created_at", -1)],
    )
    return serialize_prompt_iteration(row) if row else None


def recent_prompt_iterations(
    db: Database,
    *,
    session_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not hasattr(db, "session_continuity"):
        return []
    return [
        serialize_prompt_iteration(row)
        for row in db.session_continuity.find({"session_id": session_id})
        .sort("created_at", -1)
        .limit(bounded_limit(limit))
    ]


def session_continuity(db: Database, *, session_id: str, limit: int = 5) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "latest": latest_prompt_iteration(db, session_id),
        "recent": recent_prompt_iterations(db, session_id=session_id, limit=limit),
    }


def answer_preview(answer_text: Any) -> str | None:
    if not isinstance(answer_text, str):
        return None
    stripped = answer_text.strip()
    if len(stripped) <= ANSWER_PREVIEW_CHARS:
        return stripped
    return stripped[:ANSWER_PREVIEW_CHARS].rstrip() + "..."


def process_trace_summary(process_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for step in process_trace[:TRACE_SUMMARY_LIMIT]:
        output = step.get("output") if isinstance(step, dict) else {}
        summary.append(
            {
                "step": step.get("step"),
                "ok": output.get("ok") if isinstance(output, dict) else None,
                "reason": output.get("reason") if isinstance(output, dict) else None,
                "error": output.get("error") if isinstance(output, dict) else None,
            }
        )
    return summary


def skipped_summary(skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bounded summary of chunks that were retrieved/considered but not included
    in the final context (e.g. dropped under the character budget). Lets the
    continuity record show what was left out, not only what was used."""
    summary = []
    for item in skipped[:SKIPPED_SUMMARY_LIMIT]:
        if not isinstance(item, dict):
            continue
        summary.append(
            {
                "node_id": stringify_id(item.get("node_id")),
                "role": item.get("role"),
                "reason": item.get("reason"),
            }
        )
    return summary


def serialize_prompt_iteration(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "iteration_id": str(row["_id"]),
        "session_id": row.get("session_id"),
        "project_domain_id": row.get("project_domain_id"),
        "conversation_domain_id": row.get("conversation_domain_id"),
        "exchange_id": stringify_id(row.get("exchange_id")),
        "query": row.get("query"),
        "focus_node_id": stringify_id(row.get("focus_node_id")),
        "used_node_ids": [stringify_id(value) for value in row.get("used_node_ids", [])],
        "active_document_ids": [
            stringify_id(value) for value in row.get("active_document_ids", [])
        ],
        "prompt_budget": row.get("prompt_budget", {}),
        "context_metadata": row.get("context_metadata", {}),
        "controller_decision": row.get("controller_decision"),
        "evidence_summary": row.get("evidence_summary"),
        "answer_preview": row.get("answer_preview"),
        "answer_adapter": row.get("answer_adapter"),
        "answer_model": row.get("answer_model"),
        "process_trace_summary": row.get("process_trace_summary", []),
        "skipped_summary": row.get("skipped_summary", []),
        "is_latest": bool(row.get("is_latest")),
        "created_at": isoformat(row.get("created_at")),
        "updated_at": isoformat(row.get("updated_at")),
        "superseded_at": isoformat(row.get("superseded_at")),
    }


def render_session_continuity_text(payload: dict[str, Any]) -> str:
    latest = payload.get("latest")
    lines = [f"Session Continuity: {payload.get('session_id')}"]
    if not latest:
        lines.append("- No prompt iteration has been saved for this session.")
        return "\n".join(lines)
    lines.extend(
        [
            f"- Latest exchange: {latest.get('exchange_id')}",
            f"- Prompt: {latest.get('query')}",
            f"- Answer preview: {latest.get('answer_preview') or '(none)'}",
            f"- Focus node: {latest.get('focus_node_id') or '(none)'}",
            f"- Used nodes: {len(latest.get('used_node_ids') or [])}",
            f"- Active documents: {len(latest.get('active_document_ids') or [])}",
        ]
    )
    decision = latest.get("controller_decision") or {}
    if decision:
        lines.append(
            "- Controller decision: "
            f"{decision.get('action') or decision.get('retrieval_status') or 'recorded'}"
        )
        if decision.get("reason"):
            lines.append(f"  Reason: {decision['reason']}")
    evidence = latest.get("evidence_summary") or {}
    if evidence:
        lines.append(
            "- Evidence: "
            f"{evidence.get('included_node_count', 0)} included node(s), "
            f"{len(evidence.get('source_documents') or [])} source document(s)."
        )
    skipped = latest.get("skipped_summary") or []
    if skipped:
        lines.append(f"- Considered but not included: {len(skipped)} node(s).")
    return "\n".join(lines)


def render_restart_markdown(payload: dict[str, Any]) -> str:
    """Render a human-readable restart-state document from a `session_continuity`
    payload (see :func:`session_continuity`).

    The database is the source of truth for restart state; this is the rendered
    ".restart.md" view (build-roadmap Stage 5), generated on demand from the
    continuity-state records rather than maintained by hand.
    """
    session_id = payload.get("session_id")
    latest = payload.get("latest")
    recent = payload.get("recent") or []

    lines = [
        f"# Restart State — session `{session_id}`",
        "",
        "_Rendered from the `session_continuity` collection; the database is the "
        "source of truth for restart state._",
        "",
    ]
    if not latest:
        lines.append("No prompt iteration has been saved for this session yet.")
        return "\n".join(lines) + "\n"

    lines += [
        "## Latest exchange",
        "",
        f"- **Exchange:** `{latest.get('exchange_id')}`",
        f"- **Prompt:** {latest.get('query') or '(none)'}",
        f"- **Answer preview:** {latest.get('answer_preview') or '(none)'}",
        f"- **Focus node:** {latest.get('focus_node_id') or '(none)'}",
        f"- **Used nodes:** {len(latest.get('used_node_ids') or [])}",
        f"- **Active documents:** {len(latest.get('active_document_ids') or [])}",
    ]
    adapter, model = latest.get("answer_adapter"), latest.get("answer_model")
    if adapter or model:
        lines.append(f"- **Answer adapter/model:** {adapter or '(none)'} / {model or '(none)'}")
    if latest.get("created_at"):
        lines.append(f"- **Recorded at:** {latest.get('created_at')}")

    decision = latest.get("controller_decision") or {}
    if decision:
        action = decision.get("action") or decision.get("retrieval_status") or "recorded"
        line = f"- **Controller decision:** {action}"
        if decision.get("reason"):
            line += f" — {decision['reason']}"
        lines.append(line)
    evidence = latest.get("evidence_summary") or {}
    if evidence:
        lines.append(
            "- **Evidence:** "
            f"{evidence.get('included_node_count', 0)} included node(s), "
            f"{len(evidence.get('source_documents') or [])} source document(s)"
        )

    trace = latest.get("process_trace_summary") or []
    if trace:
        lines += ["", "### Process trace", ""]
        for step in trace:
            if step.get("ok") is True:
                status = "ok"
            elif step.get("ok") is False:
                status = "not ok"
            else:
                status = "—"
            detail = step.get("error") or step.get("reason")
            lines.append(f"- `{step.get('step')}` — {status}{f' ({detail})' if detail else ''}")

    skipped = latest.get("skipped_summary") or []
    if skipped:
        lines += ["", "### Considered but not included", ""]
        for item in skipped:
            reason = item.get("reason")
            role = item.get("role") or "—"
            lines.append(f"- `{item.get('node_id')}` ({role}){f' — {reason}' if reason else ''}")

    if recent:
        lines += ["", "## Recent iterations", ""]
        for item in recent:
            marker = " *(latest)*" if item.get("is_latest") else ""
            lines.append(f"- [{item.get('created_at') or ''}] {item.get('query') or '(none)'}{marker}")

    return "\n".join(lines) + "\n"


def object_id_or_string(value: str | None) -> ObjectId | str | None:
    if value is None:
        return None
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return str(value)


def stringify_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def isoformat(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def bounded_limit(value: int, maximum: int = 20) -> int:
    return max(1, min(maximum, int(value)))
