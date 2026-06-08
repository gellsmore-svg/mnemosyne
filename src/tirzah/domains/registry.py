from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database


DEFAULT_PROJECT_DOMAIN_ID = "tirzah"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_domain_id(value: str | None, *, fallback: str = DEFAULT_PROJECT_DOMAIN_ID) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip("-")
    return cleaned or fallback


def conversation_domain_id_for_session(session_id: str | None) -> str:
    return clean_domain_id(session_id, fallback="default")


def ensure_project_domain(
    db: Database,
    *,
    domain_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    created_by: str = "system",
) -> dict[str, Any]:
    domain_id = clean_domain_id(domain_id)
    now = utc_now()
    update_set: dict[str, Any] = {"updated_at": now}
    if title is not None:
        update_set["title"] = title
    if description is not None:
        update_set["description"] = description
    db.project_domains.update_one(
        {"domain_id": domain_id},
        {
            "$set": update_set,
            "$setOnInsert": {
                "domain_id": domain_id,
                "title": title or domain_id,
                "description": description,
                "created_by": created_by,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return get_project_domain(db, domain_id) or serialize_domain_row(
        {
            "domain_id": domain_id,
            "title": title or domain_id,
            "description": description,
            "created_by": created_by,
            "created_at": now,
            **update_set,
        }
    )


def get_project_domain(db: Database, domain_id: str) -> dict[str, Any] | None:
    row = db.project_domains.find_one({"domain_id": clean_domain_id(domain_id)}, {"_id": 0})
    return serialize_domain_row(row) if row else None


def list_project_domains(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.project_domains.find({}, {"_id": 0}).sort("domain_id", 1).limit(bounded_domain_limit(limit))
    return [serialize_domain_row(row) for row in rows]


def ensure_conversation_domain(
    db: Database,
    *,
    domain_id: str | None = None,
    project_domain_id: str | None = None,
    title: str | None = None,
    session_id: str | None = None,
    created_by: str = "system",
) -> dict[str, Any]:
    project_domain_id = clean_domain_id(project_domain_id)
    domain_id = clean_domain_id(domain_id, fallback=conversation_domain_id_for_session(session_id))
    ensure_project_domain(db, domain_id=project_domain_id, created_by=created_by)
    now = utc_now()
    update_set: dict[str, Any] = {
        "project_domain_id": project_domain_id,
        "session_id": session_id,
        "updated_at": now,
    }
    if title is not None:
        update_set["title"] = title
    db.conversation_domains.update_one(
        {"domain_id": domain_id},
        {
            "$set": update_set,
            "$setOnInsert": {
                "domain_id": domain_id,
                "title": title or domain_id,
                "created_by": created_by,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return get_conversation_domain(db, domain_id) or serialize_domain_row(
        {
            "domain_id": domain_id,
            "title": title or domain_id,
            "created_by": created_by,
            "created_at": now,
            **update_set,
        }
    )


def get_conversation_domain(db: Database, domain_id: str) -> dict[str, Any] | None:
    row = db.conversation_domains.find_one({"domain_id": clean_domain_id(domain_id)}, {"_id": 0})
    return serialize_domain_row(row) if row else None


def list_conversation_domains(
    db: Database,
    *,
    project_domain_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if project_domain_id:
        query["project_domain_id"] = clean_domain_id(project_domain_id)
    if session_id:
        query["session_id"] = session_id
    rows = (
        db.conversation_domains.find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(bounded_domain_limit(limit))
    )
    return [serialize_domain_row(row) for row in rows]


def serialize_domain_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: serialize_domain_value(value)
        for key, value in row.items()
        if key != "_id"
    }


def serialize_domain_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_domain_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_domain_value(item) for key, item in value.items() if key != "_id"}
    return value


def bounded_domain_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 100))
