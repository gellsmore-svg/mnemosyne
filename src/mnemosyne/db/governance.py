from __future__ import annotations

from typing import Any

from pymongo.database import Database


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


def list_governance_rows(collection: Any, sort_field: str, limit: int) -> list[dict[str, Any]]:
    rows = collection.find({}, {"_id": 0}).sort(sort_field, 1).limit(bounded_governance_limit(limit))
    return [serialize_governance_row(row) for row in rows]


def get_governance_row(collection: Any, key: str, value: str) -> dict[str, Any] | None:
    if not value:
        return None
    row = collection.find_one({key: value}, {"_id": 0})
    return serialize_governance_row(row) if row else None


def serialize_governance_row(row: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(row)
    for key, value in list(serialized.items()):
        if hasattr(value, "isoformat"):
            serialized[key] = value.isoformat()
    return serialized


def bounded_governance_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 100))
