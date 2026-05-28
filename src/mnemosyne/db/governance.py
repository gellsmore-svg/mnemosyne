from __future__ import annotations

from typing import Any

from pymongo.database import Database


def list_agent_identities(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.agent_identities, "identity_id", limit)


def list_trust_weighting_profiles(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.trust_weighting_profiles, "weighting_profile_id", limit)


def list_governance_policies(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.governance_policies, "policy_id", limit)


def list_process_objects(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    return list_governance_rows(db.process_objects, "process_id", limit)


def list_governance_rows(collection: Any, sort_field: str, limit: int) -> list[dict[str, Any]]:
    rows = collection.find({}, {"_id": 0}).sort(sort_field, 1).limit(bounded_governance_limit(limit))
    return [serialize_governance_row(row) for row in rows]


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
