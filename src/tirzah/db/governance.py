from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database

PROCESS_RUN_STATUSES = {
    "pending",
    "active",
    "completed",
    "blocked",
    "exception_requested",
    "abandoned",
}


def list_agent_identities(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.agent_identities, "identity_id", limit)


def get_agent_identity(db: Database, identity_id: str) -> dict[str, Any] | None:
    return get_governance_row(db.agent_identities, "identity_id", identity_id)


def list_trust_weighting_profiles(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.trust_weighting_profiles, "weighting_profile_id", limit)


def get_trust_weighting_profile(db: Database, weighting_profile_id: str) -> dict[str, Any] | None:
    return get_governance_row(
        db.trust_weighting_profiles,
        "weighting_profile_id",
        weighting_profile_id,
    )


def list_governance_policies(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.governance_policies, "policy_id", limit)


def get_governance_policy(db: Database, policy_id: str) -> dict[str, Any] | None:
    return get_governance_row(db.governance_policies, "policy_id", policy_id)


def list_process_objects(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.process_objects, "process_id", limit)


def get_process_object(db: Database, process_id: str) -> dict[str, Any] | None:
    return get_governance_row(db.process_objects, "process_id", process_id)


def list_process_runs(
    db: Database,
    *,
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if session_id:
        filters["session_id"] = session_id
    if status:
        filters["status"] = status
    rows = (
        db.process_runs.find(filters, {"_id": 0})
        .sort("updated_at", -1)
        .limit(bounded_governance_limit(limit))
    )
    return [serialize_governance_row(row) for row in rows]


def get_process_run(db: Database, run_id: str) -> dict[str, Any] | None:
    return get_governance_row(db.process_runs, "run_id", run_id)


def create_process_run(
    db: Database,
    *,
    process_id: str,
    session_id: str,
    identity_id: str | None = None,
    current_step_id: str | None = None,
    status: str = "active",
    run_id: str | None = None,
) -> dict[str, Any]:
    status = validated_process_run_status(status)
    now = datetime.now(timezone.utc)
    row = {
        "run_id": run_id or f"process_run_{uuid4().hex}",
        "process_id": process_id,
        "session_id": session_id,
        "identity_id": identity_id,
        "status": status,
        "current_step_id": current_step_id,
        "completed_steps": [],
        "exceptions": [],
        "exchange_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    db.process_runs.insert_one(row)
    return serialize_governance_row(row)


def update_process_run(
    db: Database,
    run_id: str,
    *,
    status: str | None = None,
    current_step_id: str | None = None,
    completed_step_id: str | None = None,
    exchange_id: str | None = None,
    exception: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = db.process_runs.find_one({"run_id": run_id})
    if not existing:
        return None
    now = datetime.now(timezone.utc)
    set_fields: dict[str, Any] = {"updated_at": now}
    if status is not None:
        set_fields["status"] = validated_process_run_status(status)
    if current_step_id is not None:
        set_fields["current_step_id"] = current_step_id
    push_fields: dict[str, Any] = {}
    if completed_step_id:
        push_fields["completed_steps"] = {"step_id": completed_step_id, "completed_at": now}
    if exchange_id:
        push_fields["exchange_ids"] = exchange_id
    if exception:
        push_fields["exceptions"] = {**exception, "timestamp": now}
    update: dict[str, Any] = {"$set": set_fields}
    if push_fields:
        update["$push"] = push_fields
    db.process_runs.update_one({"run_id": run_id}, update)
    return get_process_run(db, run_id)


def list_governance_rows(collection: Any, sort_field: str, limit: int) -> list[dict[str, Any]]:
    rows = collection.find({}, {"_id": 0}).sort(sort_field, 1).limit(bounded_governance_limit(limit))
    return [serialize_governance_row(row) for row in rows]


def get_governance_row(collection: Any, key: str, value: str) -> dict[str, Any] | None:
    if not value:
        return None
    row = collection.find_one({key: value}, {"_id": 0})
    return serialize_governance_row(row) if row else None


def serialize_governance_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: serialize_governance_value(value)
        for key, value in row.items()
        if key != "_id"
    }


def serialize_governance_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_governance_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: serialize_governance_value(item)
            for key, item in value.items()
            if key != "_id"
        }
    return value


def validated_process_run_status(status: str) -> str:
    if status not in PROCESS_RUN_STATUSES:
        raise ValueError(f"Unsupported process run status: {status}")
    return status


def bounded_governance_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 100))
