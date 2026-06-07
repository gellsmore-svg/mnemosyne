from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(
    db: Database,
    title: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    resolved_title = clean_title(title) or "New Session"
    resolved_session_id = clean_session_id(session_id) or generate_session_id(resolved_title)
    db.sessions.update_one(
        {"session_id": resolved_session_id},
        {
            "$setOnInsert": {
                "schema_version": 1,
                "session_id": resolved_session_id,
                "title": resolved_title,
                "created_at": now,
                "exchange_count": 0,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
    )
    row = db.sessions.find_one({"session_id": resolved_session_id})
    return serialize_session(row)


def touch_session(db: Database, session_id: str, title: str | None = None) -> None:
    now = utc_now()
    resolved_session_id = clean_session_id(session_id) or "default"
    resolved_title = clean_title(title) or title_from_session_id(resolved_session_id)
    db.sessions.update_one(
        {"session_id": resolved_session_id},
        {
            "$setOnInsert": {
                "schema_version": 1,
                "session_id": resolved_session_id,
                "title": resolved_title,
                "created_at": now,
            },
            "$set": {
                "updated_at": now,
                "last_exchange_at": now,
            },
            "$inc": {"exchange_count": 1},
        },
        upsert=True,
    )


def list_sessions(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    sessions = [serialize_session(row) for row in db.sessions.find({}).sort("updated_at", -1).limit(limit)]
    known_ids = {session["session_id"] for session in sessions}
    for session_id in db.exchanges.distinct("session_id"):
        if session_id and session_id not in known_ids and len(sessions) < limit:
            sessions.append(legacy_session_summary(db, session_id))
            known_ids.add(session_id)
    return sessions


def legacy_session_summary(db: Database, session_id: str) -> dict[str, Any]:
    latest = db.exchanges.find_one({"session_id": session_id}, sort=[("created_at", -1)])
    return {
        "session_id": session_id,
        "title": title_from_session_id(session_id),
        "exchange_count": db.exchanges.count_documents({"session_id": session_id}),
        "created_at": None,
        "updated_at": latest.get("created_at").isoformat() if latest else None,
        "last_exchange_at": latest.get("created_at").isoformat() if latest else None,
        "legacy": True,
    }


def serialize_session(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "session_id": row.get("session_id"),
        "title": row.get("title") or title_from_session_id(row.get("session_id", "")),
        "exchange_count": row.get("exchange_count", 0),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        "last_exchange_at": row.get("last_exchange_at").isoformat()
        if row.get("last_exchange_at")
        else None,
        "legacy": False,
    }


def clean_title(title: str | None) -> str | None:
    if not title:
        return None
    return " ".join(title.split())[:80] or None


def clean_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", session_id.strip())
    return cleaned.strip("-")[:100] or None


def generate_session_id(title: str) -> str:
    slug = clean_session_id(title.lower()) or "session"
    return f"{slug}-{uuid4().hex[:8]}"


def title_from_session_id(session_id: str) -> str:
    if not session_id:
        return "Default"
    return session_id.replace("-", " ").replace("_", " ").title()
