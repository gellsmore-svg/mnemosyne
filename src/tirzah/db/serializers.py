from __future__ import annotations

from typing import Any


def serialize_queue_job(job: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(job)
    serialized["_id"] = str(serialized["_id"])
    if serialized.get("existing_document_id"):
        serialized["existing_document_id"] = str(serialized["existing_document_id"])
    if serialized.get("existing_queue_id"):
        serialized["existing_queue_id"] = str(serialized["existing_queue_id"])
    if serialized.get("result"):
        result = dict(serialized["result"])
        if result.get("document_id"):
            result["document_id"] = str(result["document_id"])
        serialized["result"] = result
    for field in ("created_at", "updated_at"):
        if serialized.get(field):
            serialized[field] = serialized[field].isoformat()
    return serialized


def serialize_queue_summary(summary: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(summary)
    if serialized.get("oldest_pending"):
        oldest_pending = dict(serialized["oldest_pending"])
        oldest_pending["_id"] = str(oldest_pending["_id"])
        if oldest_pending.get("created_at"):
            oldest_pending["created_at"] = oldest_pending["created_at"].isoformat()
        serialized["oldest_pending"] = oldest_pending
    return serialized
