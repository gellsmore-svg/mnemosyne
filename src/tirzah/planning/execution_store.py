"""Persist interpretive PLAN execution state for resume (SPEC §4.6)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tirzah.planning.recursive import CairnPlan, PlanStep


EXECUTION_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def execution_key(plan_id: str, revision: int, session_id: str) -> dict[str, Any]:
    return {"plan_id": plan_id, "revision": int(revision), "session_id": session_id}


def load_plan_execution(db, plan_id: str, revision: int, session_id: str) -> dict[str, Any] | None:
    collection = getattr(db, "plan_executions", None)
    if collection is None:
        return None
    row = collection.find_one({**execution_key(plan_id, revision, session_id), "status": "running"})
    if not row:
        return None
    return serialize_execution_row(row)


def save_plan_execution(
    db,
    *,
    plan: CairnPlan,
    session_id: str,
    query: str,
    steps: list[PlanStep],
    completed_step_ids: list[str],
    artifacts: dict[str, Any],
    trace: list[dict[str, Any]],
    effects: list[str],
    status: str,
    execution_id: str | None = None,
) -> str | None:
    collection = getattr(db, "plan_executions", None)
    if collection is None:
        return None
    now = utc_now()
    key = execution_key(plan.plan_id, plan.revision, session_id)
    row = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "execution_id": execution_id or f"pexec_{uuid4().hex}",
        **key,
        "query": query,
        "status": status,
        "steps": [step.__dict__ if hasattr(step, "__dict__") else step for step in steps],
        "completed_step_ids": list(completed_step_ids),
        "artifacts": artifacts,
        "trace": trace,
        "effects": list(effects),
        "updated_at": now,
    }
    collection.update_one(key, {"$set": row, "$setOnInsert": {"created_at": now}}, upsert=True)
    return row["execution_id"]


def finalize_plan_execution(db, plan_id: str, revision: int, session_id: str, *, status: str) -> None:
    collection = getattr(db, "plan_executions", None)
    if collection is None:
        return
    collection.update_one(
        execution_key(plan_id, revision, session_id),
        {"$set": {"status": status, "updated_at": utc_now()}},
    )


def list_plan_executions(db, session_id: str, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    collection = getattr(db, "plan_executions", None)
    if collection is None:
        return []
    query: dict[str, Any] = {"session_id": session_id}
    if status:
        query["status"] = status
    rows = collection.find(query, {"_id": 0}).sort("updated_at", -1).limit(max(1, min(limit, 100)))
    return [serialize_execution_row(row) for row in rows]


def resume_steps_and_context(saved: dict[str, Any]) -> tuple[list[PlanStep], set[str], dict[str, Any], list[dict[str, Any]], set[str]]:
    steps = []
    for item in saved.get("steps") or []:
        if isinstance(item, PlanStep):
            steps.append(item)
        elif isinstance(item, dict):
            steps.append(PlanStep(**item))
    completed = {str(value) for value in saved.get("completed_step_ids") or []}
    artifacts = dict(saved.get("artifacts") or {})
    trace = list(saved.get("trace") or [])
    effects = {str(value) for value in saved.get("effects") or []}
    for step in steps:
        if step.status == "active":
            step.status = "pending"
    return steps, completed, artifacts, trace, effects


def serialize_execution_row(row: dict[str, Any]) -> dict[str, Any]:
    data = {key: value for key, value in row.items() if key != "_id"}
    if hasattr(data.get("updated_at"), "isoformat"):
        data["updated_at"] = data["updated_at"].isoformat()
    if hasattr(data.get("created_at"), "isoformat"):
        data["created_at"] = data["created_at"].isoformat()
    return data