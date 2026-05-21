from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from mnemosyne.sessions.active_documents import valid_object_ids


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_node_usage(db: Database, node_ids: list[str]) -> int:
    if not hasattr(db, "nodes") or not hasattr(db.nodes, "update_many"):
        return 0
    object_ids = valid_object_ids(node_ids)
    if not object_ids:
        return 0
    result = db.nodes.update_many(
        {
            "_id": {"$in": object_ids},
            "endorsement_label": {"$ne": "rejected"},
        },
        {
            "$inc": {"usage_score": 1},
            "$set": {"last_used_at": utc_now()},
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)
