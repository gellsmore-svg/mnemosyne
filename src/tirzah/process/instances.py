"""Process instances — a template version applied to a specific task.

An instance (``process_instances`` collection) pins one template *version* to a
task (or feature/bug/body of work) and carries the runtime state of that work
under the process: its status, its own append-only event trace (steps, human
interactions, deviations, gate waits, override, outcome), and a binding back to
the sessions that executed under it.

Binding the *version* (not just the template id) is deliberate: a template can
evolve, but an in-flight instance keeps executing under the process text it
started with (backward compatibility for active instances).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database

from tirzah.db.governance import serialize_governance_row
from tirzah.process.templates import get_template

INSTANCE_STATUSES = (
    "active",       # running under the process
    "awaiting_gate",  # paused at a human gate, resumable on approval
    "completed",    # finished; retrospective available
    "abandoned",    # stopped without completing
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start_instance(
    db: Database,
    *,
    template_id: str,
    task: str,
    session_id: str | None = None,
    version: int | None = None,
    selected_by: str = "operator",
    selection_reason: str = "manual",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a template (its latest version, or a pinned one) to a task.

    ``selection_reason`` records how the process was chosen (manual / default /
    suggested / override) for the audit trail. Returns the instance document.
    """
    template = get_template(db, template_id, version=version)
    if template is None:
        raise ValueError(f"unknown template/version: {template_id} v{version}")
    now = _utcnow()
    document = {
        "instance_id": f"proc_inst_{uuid4().hex[:12]}",
        "template_id": template_id,
        "template_version": int(template["version"]),
        "template_name": template["name"],
        "process_body": template["body"],  # frozen at bind time
        "task": task,
        "session_id": session_id,
        "status": "active",
        "selected_by": selected_by,
        "selection_reason": selection_reason,
        "metadata": dict(metadata or {}),
        "trace": [],
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "outcome": None,
    }
    _append(document, "process.instance.started", {
        "template_id": template_id,
        "template_version": document["template_version"],
        "selection_reason": selection_reason,
    }, at=now)
    db.process_instances.insert_one({**document})
    return serialize_governance_row(document)


def get_instance(db: Database, instance_id: str) -> dict[str, Any] | None:
    row = db.process_instances.find_one({"instance_id": instance_id}, {"_id": 0})
    return serialize_governance_row(row) if row else None


def active_instance_for_session(db: Database, session_id: str) -> dict[str, Any] | None:
    """The most recent non-terminal instance bound to a session (the 'active
    process' for that conversation), or None."""
    rows = [
        row
        for row in db.process_instances.find({"session_id": session_id}, {"_id": 0})
        if row.get("status") in ("active", "awaiting_gate")
    ]
    if not rows:
        return None
    latest = max(rows, key=lambda row: str(row.get("started_at") or ""))
    return serialize_governance_row(latest)


def list_instances(
    db: Database,
    *,
    template_id: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if template_id:
        query["template_id"] = template_id
    if status:
        query["status"] = status
    if session_id:
        query["session_id"] = session_id
    rows = list(db.process_instances.find(query, {"_id": 0}))
    rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    return [serialize_governance_row(row) for row in rows[: max(1, min(int(limit), 500))]]


def record_event(
    db: Database,
    instance_id: str,
    event: str,
    detail: dict[str, Any] | None = None,
    *,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Append one event to an instance's trace (and optionally set its status).

    This is the single write path for everything the enforcement layer records:
    gate waits, approvals, deviations, overrides, step outcomes.
    """
    now = _utcnow()
    entry = {"event": event, "detail": dict(detail or {}), "at": now.isoformat()}
    update: dict[str, Any] = {"$push": {"trace": entry}, "$set": {"updated_at": now}}
    if status is not None:
        if status not in INSTANCE_STATUSES:
            raise ValueError(f"invalid instance status: {status!r}")
        update["$set"]["status"] = status
    db.process_instances.update_one({"instance_id": instance_id}, update)
    return get_instance(db, instance_id)


def complete_instance(
    db: Database,
    instance_id: str,
    *,
    outcome: str = "completed",
    note: str | None = None,
) -> dict[str, Any] | None:
    """Mark an instance complete and capture its outcome (retrospective input)."""
    now = _utcnow()
    db.process_instances.update_one(
        {"instance_id": instance_id},
        {
            "$set": {
                "status": "completed",
                "outcome": outcome,
                "completed_at": now,
                "updated_at": now,
            },
            "$push": {
                "trace": {
                    "event": "process.instance.completed",
                    "detail": {"outcome": outcome, "note": note},
                    "at": now.isoformat(),
                }
            },
        },
    )
    return get_instance(db, instance_id)


def abandon_instance(
    db: Database, instance_id: str, *, reason: str | None = None
) -> dict[str, Any] | None:
    now = _utcnow()
    db.process_instances.update_one(
        {"instance_id": instance_id},
        {
            "$set": {"status": "abandoned", "updated_at": now},
            "$push": {
                "trace": {
                    "event": "process.instance.abandoned",
                    "detail": {"reason": reason},
                    "at": now.isoformat(),
                }
            },
        },
    )
    return get_instance(db, instance_id)


def _append(document: dict[str, Any], event: str, detail: dict[str, Any], *, at: datetime) -> None:
    document["trace"].append({"event": event, "detail": detail, "at": at.isoformat()})
