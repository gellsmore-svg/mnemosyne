from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database


OUTPUT_INGESTION_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def answer_output_text(answer: dict[str, Any]) -> str:
    return str(answer.get("answer") or "").strip()


def output_content_hash(session_id: str, query: str, answer_text: str) -> str:
    payload = "\n".join([session_id, query.strip(), answer_text])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def queue_exchange_output(
    db: Database,
    exchange_id: str,
    session_id: str,
    query: str,
    answer: dict[str, Any],
    used_node_ids: list[str],
    active_document_ids: list[str],
) -> str | None:
    answer_text = answer_output_text(answer)
    if not answer_text:
        return None
    if not hasattr(db, "output_ingestion_queue"):
        return None

    now = utc_now()
    job = {
        "schema_version": OUTPUT_INGESTION_SCHEMA_VERSION,
        "status": "pending",
        "source_type": "llm_answer",
        "exchange_id": exchange_id,
        "session_id": session_id,
        "query": query,
        "answer_text": answer_text,
        "answer_adapter": answer.get("adapter"),
        "answer_model": answer.get("model"),
        "used_node_ids": used_node_ids,
        "active_document_ids": active_document_ids,
        "content_hash_sha256": output_content_hash(session_id, query, answer_text),
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = db.output_ingestion_queue.insert_one(job)
    return str(result.inserted_id)


def output_ingestion_filter_query(
    status: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    if session_id:
        query["session_id"] = session_id
    return query


def list_output_ingestion_jobs(
    db: Database,
    limit: int = 20,
    status: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    if not hasattr(db, "output_ingestion_queue"):
        return []
    rows = (
        db.output_ingestion_queue.find(
            output_ingestion_filter_query(status=status, session_id=session_id)
        )
        .sort("created_at", -1)
        .limit(bounded_limit(limit))
    )
    return [serialize_output_ingestion_job(row) for row in rows]


def bounded_limit(value: int, maximum: int = 100) -> int:
    return max(1, min(maximum, int(value)))


def serialize_output_ingestion_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(row["_id"]),
        "schema_version": row.get("schema_version"),
        "status": row.get("status"),
        "source_type": row.get("source_type"),
        "exchange_id": row.get("exchange_id"),
        "session_id": row.get("session_id"),
        "query": row.get("query"),
        "answer_preview": str(row.get("answer_text") or "")[:500],
        "answer_adapter": row.get("answer_adapter"),
        "answer_model": row.get("answer_model"),
        "used_node_ids": row.get("used_node_ids", []),
        "active_document_ids": row.get("active_document_ids", []),
        "content_hash_sha256": row.get("content_hash_sha256"),
        "attempts": row.get("attempts", 0),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }
