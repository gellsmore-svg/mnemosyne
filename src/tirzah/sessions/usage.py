from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from tirzah.db.memory_store import MemoryStore, as_memory_store
from tirzah.sessions.active_documents import valid_object_ids


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_node_usage(db: Database | MemoryStore, node_ids: list[str]) -> int:
    store = as_memory_store(db)
    if not hasattr(store.db, "nodes"):
        return 0
    object_ids = valid_object_ids(node_ids)
    if not object_ids:
        return 0
    return store.update_nodes(
        {
            "_id": {"$in": object_ids},
            "endorsement_label": {"$ne": "rejected"},
        },
        {
            "$inc": {"usage_score": 1},
            "$set": {"last_used_at": utc_now()},
        },
    )
