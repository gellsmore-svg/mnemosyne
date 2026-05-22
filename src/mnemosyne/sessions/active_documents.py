from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_active_documents(
    db: Database,
    session_id: str,
    node_ids: list[str],
) -> list[str]:
    object_ids = valid_object_ids(node_ids)
    if not object_ids or not hasattr(db, "active_documents"):
        return []
    nodes = list(
        db.nodes.find(
            {"_id": {"$in": object_ids}},
            {"document_id": 1, "title": 1, "labels": 1, "provenance": 1},
        )
    )
    if not nodes:
        return []
    documents = documents_by_id(db, [node["document_id"] for node in nodes if node.get("document_id")])
    by_document: dict[str, dict[str, Any]] = {}
    grouped_node_ids: dict[str, set[str]] = defaultdict(set)
    grouped_labels: dict[str, set[str]] = defaultdict(set)
    now = utc_now()
    for node in nodes:
        document_id = node.get("document_id")
        if not document_id:
            continue
        key = str(document_id)
        grouped_node_ids[key].add(str(node["_id"]))
        grouped_labels[key].update(node.get("labels") or [])
        document = documents.get(key, {})
        by_document[key] = {
            "title": document.get("title") or node.get("title"),
            "source": document.get("source") or {},
        }
    for document_id, metadata in by_document.items():
        db.active_documents.update_one(
            {"session_id": session_id, "document_id": document_id},
            {
                "$setOnInsert": {
                    "schema_version": 1,
                    "session_id": session_id,
                    "document_id": document_id,
                    "first_referenced_at": now,
                },
                "$set": {
                    "title": metadata.get("title"),
                    "source": metadata.get("source") or {},
                    "last_referenced_at": now,
                },
                "$addToSet": {
                    "node_ids": {"$each": sorted(grouped_node_ids[document_id])},
                    "labels": {"$each": sorted(grouped_labels[document_id])},
                },
                "$inc": {"reference_count": 1},
            },
            upsert=True,
        )
    return sorted(by_document)


def valid_object_ids(values: list[str]) -> list[ObjectId]:
    object_ids = []
    seen = set()
    for value in values:
        try:
            object_id = ObjectId(value)
        except (InvalidId, TypeError):
            continue
        key = str(object_id)
        if key in seen:
            continue
        seen.add(key)
        object_ids.append(object_id)
    return object_ids


def documents_by_id(db: Database, document_ids: list[ObjectId]) -> dict[str, dict[str, Any]]:
    if not document_ids:
        return {}
    return {
        str(document["_id"]): document
        for document in db.documents.find({"_id": {"$in": document_ids}}, {"title": 1, "source": 1})
    }


def list_active_documents(db: Database, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    if not hasattr(db, "active_documents"):
        return []
    rows = db.active_documents.find({"session_id": session_id}).sort("last_referenced_at", -1).limit(limit)
    return [serialize_active_document(row) for row in rows]


def serialize_active_document(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row.get("session_id"),
        "document_id": row.get("document_id"),
        "title": row.get("title"),
        "source": row.get("source") or {},
        "labels": sorted(row.get("labels") or []),
        "node_ids": sorted(row.get("node_ids") or []),
        "reference_count": row.get("reference_count", 0),
        "first_referenced_at": row.get("first_referenced_at").isoformat()
        if row.get("first_referenced_at")
        else None,
        "last_referenced_at": row.get("last_referenced_at").isoformat()
        if row.get("last_referenced_at")
        else None,
    }
