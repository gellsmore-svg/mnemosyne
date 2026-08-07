"""Deborah open-questions store on the family Mongo (Tirzah estate).

Deborah records residual uncertainty as first-class open questions. When the
estate shares Tirzah's Mongo, they land in ``deborah_open_questions`` so
operators and tools can query them beside memory nodes.

Fail-soft: never raise into the Deborah harness.
"""

from __future__ import annotations

from typing import Any

OPEN_QUESTIONS_COLLECTION = "deborah_open_questions"


def ensure_open_question_indexes(db: Any) -> bool:
    """Create indexes for plan_id / created_at lookups. Best-effort."""
    if db is None:
        return False
    try:
        col = db[OPEN_QUESTIONS_COLLECTION]
        col.create_index([("plan_id", 1), ("created_at", -1)], name="plan_timeline")
        col.create_index([("open_question_id", 1)], name="oq_id", unique=True)
        return True
    except Exception:
        return False


def record_open_question(db: Any, document: dict[str, Any]) -> dict[str, Any] | None:
    """Insert one open-question document. Returns the doc or None on failure."""
    if db is None or not isinstance(document, dict):
        return None
    try:
        ensure_open_question_indexes(db)
        payload = dict(document)
        db[OPEN_QUESTIONS_COLLECTION].insert_one(payload)
        return payload
    except Exception:
        return None


def list_open_questions(
    db: Any,
    *,
    plan_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List open questions, newest first. Empty list if unavailable."""
    if db is None:
        return []
    try:
        query: dict[str, Any] = {}
        if plan_id:
            query["plan_id"] = plan_id
        cursor = db[OPEN_QUESTIONS_COLLECTION].find(query, {"_id": 0})
        try:
            rows = list(cursor.sort([("created_at", -1)]).limit(max(1, int(limit))))
        except (AttributeError, TypeError):
            rows = list(cursor)
            rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
            rows = rows[: max(1, int(limit))]
        return rows
    except Exception:
        return []


def try_get_database(*, database: str | None = None) -> Any | None:
    """Bootstrap Tirzah's configured Mongo, or None if unreachable."""
    try:
        from tirzah.config import load_config
        from tirzah.db.client import get_database

        cfg = load_config().mongo
        if database:
            # Lightweight override without mutating global config objects.
            from dataclasses import replace

            try:
                cfg = replace(cfg, database=database)
            except TypeError:
                # Not a dataclass — fall through with original cfg.
                pass
        return get_database(cfg)
    except Exception:
        return None
