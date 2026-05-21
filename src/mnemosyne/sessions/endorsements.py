from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database

from mnemosyne.retrieval.queries import serialize_node


ENDORSEMENT_LABELS = {
    "unreviewed",
    "implicit_endorsed",
    "explicit_endorsed",
    "rejected",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def list_generated_output_nodes(
    db: Database,
    limit: int = 20,
    endorsement_label: str | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"labels": "generated_output"}
    if endorsement_label:
        if not valid_endorsement_label(endorsement_label):
            return []
        query["endorsement_label"] = endorsement_label
    rows = db.nodes.find(query).sort("created_at", -1).limit(bounded_limit(limit))
    return [serialize_review_node(row) for row in rows]


def update_node_endorsement(
    db: Database,
    node_id: str,
    endorsement_label: str,
    reviewer: str = "user",
    note: str | None = None,
) -> dict[str, Any]:
    if not valid_endorsement_label(endorsement_label):
        return {
            "ok": False,
            "reason": "invalid_endorsement_label",
            "endorsement_label": endorsement_label,
            "allowed": sorted(ENDORSEMENT_LABELS),
        }
    object_id = parse_object_id(node_id)
    if object_id is None:
        return {"ok": False, "reason": "invalid_node_id", "node_id": node_id}

    node = db.nodes.find_one({"_id": object_id})
    if not node:
        return {"ok": False, "reason": "node_not_found", "node_id": node_id}

    now = utc_now()
    review_entry = {
        "endorsement_label": endorsement_label,
        "reviewer": reviewer,
        "note": note,
        "reviewed_at": now,
    }
    db.nodes.update_one(
        {"_id": object_id},
        {
            "$set": {
                "endorsement_label": endorsement_label,
                "provenance.endorsement_label": endorsement_label,
                "updated_at": now,
                "metadata.review": review_entry,
            },
            "$push": {"metadata.review_history": review_entry},
        },
    )
    updated = db.nodes.find_one({"_id": object_id})
    return {"ok": True, "node": serialize_review_node(updated)}


def valid_endorsement_label(label: str) -> bool:
    return label in ENDORSEMENT_LABELS


def parse_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def bounded_limit(value: int, maximum: int = 100) -> int:
    return max(1, min(maximum, int(value)))


def serialize_review_node(node: dict[str, Any] | None) -> dict[str, Any]:
    if not node:
        return {}
    serialized = serialize_node(node)
    serialized["review"] = serialize_review(node.get("metadata", {}).get("review"))
    serialized["review_history_count"] = len(node.get("metadata", {}).get("review_history", []))
    return serialized


def serialize_review(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not review:
        return None
    return {
        "endorsement_label": review.get("endorsement_label"),
        "reviewer": review.get("reviewer"),
        "note": review.get("note"),
        "reviewed_at": review.get("reviewed_at").isoformat()
        if review.get("reviewed_at")
        else None,
    }
