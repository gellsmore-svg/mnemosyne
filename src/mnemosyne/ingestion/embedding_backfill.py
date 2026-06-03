from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.database import Database

from mnemosyne.db.repositories import (
    backfill_node_embeddings,
    bounded_candidate_limit,
    count_backfill_embedding_candidates,
    embedding_backfill_activity_log,
    InvalidEmbeddingBackfillScope,
)


EMBEDDING_BACKFILL_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_embedding_backfill_job(
    db: Database,
    *,
    batch_limit: int = 100,
    label: str | None = None,
    document_id: str | None = None,
    force: bool = False,
    created_by: str = "operator",
) -> dict[str, Any]:
    now = utc_now()
    scope_total = embedding_backfill_scope_total(
        db,
        label=label,
        document_id=document_id,
        force=force,
    )
    job = {
        "schema_version": EMBEDDING_BACKFILL_SCHEMA_VERSION,
        "status": "pending",
        "batch_limit": bounded_candidate_limit(batch_limit, maximum=1000),
        "label": str(label).strip() if label else None,
        "document_id": str(document_id).strip() if document_id else None,
        "force": bool(force),
        "scope_total": scope_total,
        "created_by": created_by,
        "batch_count": 0,
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "last_node_id": None,
        "last_result": None,
        "transaction_boundary": "batch_cursor_after_completed_batch",
        "node_write_boundary": "individual_node_update",
        "reason": None,
        "created_at": now,
        "updated_at": now,
    }
    result = db.embedding_backfill_jobs.insert_one(job)
    job["_id"] = result.inserted_id
    return serialize_embedding_backfill_job(job)


def embedding_backfill_scope_total(
    db: Database,
    *,
    label: str | None = None,
    document_id: str | None = None,
    force: bool = False,
) -> int | None:
    try:
        return count_backfill_embedding_candidates(
            db,
            label=label,
            document_id=document_id,
            force=force,
        )
    except InvalidEmbeddingBackfillScope:
        return None


def list_embedding_backfill_jobs(
    db: Database,
    *,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    rows = (
        db.embedding_backfill_jobs.find(query)
        .sort("updated_at", -1)
        .limit(bounded_candidate_limit(limit, maximum=100))
    )
    return [serialize_embedding_backfill_job(row) for row in rows]


def requeue_processing_embedding_backfill_job(
    db: Database,
    job_id: str,
    *,
    reason: str = "operator_requeued_processing_job",
    actor: str = "operator",
) -> dict[str, Any]:
    object_id = parse_job_object_id(job_id)
    if object_id is None:
        return {
            "ok": False,
            "status": "invalid_job_id",
            "reason": "invalid_job_id",
            "job_id": job_id,
        }
    now = utc_now()
    row = db.embedding_backfill_jobs.find_one_and_update(
        {"_id": object_id, "status": "processing"},
        {
            "$set": {
                "status": "pending",
                "reason": reason,
                "requeued_at": now,
                "requeued_by": actor,
                "requeue_reason": reason,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not row:
        return {
            "ok": False,
            "status": "not_requeued",
            "reason": "job_not_processing_or_not_found",
            "job_id": job_id,
        }
    return {
        "ok": True,
        "status": "pending",
        "job": serialize_embedding_backfill_job(row),
    }


def parse_job_object_id(job_id: str) -> ObjectId | None:
    try:
        return ObjectId(str(job_id))
    except Exception:
        return None


def claim_next_embedding_backfill_job(db: Database) -> dict[str, Any] | None:
    return db.embedding_backfill_jobs.find_one_and_update(
        {"status": "pending"},
        {"$set": {"status": "processing", "updated_at": utc_now()}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def process_next_embedding_backfill_job(db: Database, embedder: Any) -> dict[str, Any]:
    job = claim_next_embedding_backfill_job(db)
    if not job:
        return {"ok": True, "status": "idle"}

    try:
        result = backfill_node_embeddings(
            db,
            embedder,
            limit=job.get("batch_limit") or 100,
            label=job.get("label"),
            document_id=job.get("document_id"),
            force=bool(job.get("force")),
            after_node_id=job.get("last_node_id"),
        )
    except Exception as error:
        result = {
            "ok": False,
            "reason": "embedding_backfill_exception",
            "error_type": error.__class__.__name__,
            "error": str(error),
            "matched_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 1,
        }
        result["activity_log"] = embedding_backfill_activity_log(result)
        block_embedding_backfill_job(db, job, result)
        return {
            "ok": False,
            "status": "blocked",
            "job_id": str(job["_id"]),
            "result": result,
        }
    status = next_embedding_backfill_status(job, result)
    update: dict[str, Any] = {
        "$set": {
            "status": status,
            "last_result": result,
            "last_node_id": result.get("last_node_id") or job.get("last_node_id"),
            "reason": result.get("reason"),
            "updated_at": utc_now(),
        },
        "$inc": {
            "batch_count": 1,
            "updated_count": int(result.get("updated_count") or 0),
            "skipped_count": int(result.get("skipped_count") or 0),
            "error_count": int(result.get("error_count") or 0),
        },
    }
    db.embedding_backfill_jobs.update_one({"_id": job["_id"]}, update)
    return {
        "ok": result.get("ok", False),
        "status": status,
        "job_id": str(job["_id"]),
        "result": result,
    }


def process_embedding_backfill_batches(
    db: Database,
    embedder: Any,
    *,
    max_batches: int = 1,
) -> dict[str, Any]:
    bounded_batches = bounded_candidate_limit(max_batches, maximum=100)
    results = []
    aggregate = {
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
    }
    final_status = "idle"
    for _ in range(bounded_batches):
        result = process_next_embedding_backfill_job(db, embedder)
        final_status = result.get("status") or "unknown"
        results.append(result)
        batch_result = result.get("result") or {}
        aggregate["updated_count"] += int(batch_result.get("updated_count") or 0)
        aggregate["skipped_count"] += int(batch_result.get("skipped_count") or 0)
        aggregate["error_count"] += int(batch_result.get("error_count") or 0)
        if final_status in {"idle", "completed", "blocked"}:
            break
    return {
        "ok": all(result.get("ok", False) for result in results) if results else True,
        "status": final_status,
        "requested_batches": bounded_batches,
        "processed_batches": len(results),
        **aggregate,
        "results": results,
    }


def next_embedding_backfill_status(job: dict[str, Any], result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "blocked"
    if int(result.get("matched_count") or 0) < int(job.get("batch_limit") or 100):
        return "completed"
    return "pending"


def block_embedding_backfill_job(
    db: Database,
    job: dict[str, Any],
    result: dict[str, Any],
) -> None:
    db.embedding_backfill_jobs.update_one(
        {"_id": job["_id"]},
        {
            "$set": {
                "status": "blocked",
                "last_result": result,
                "reason": result.get("reason"),
                "updated_at": utc_now(),
            },
            "$inc": {
                "batch_count": 1,
                "error_count": int(result.get("error_count") or 1),
            },
        },
    )


def serialize_embedding_backfill_job(row: dict[str, Any]) -> dict[str, Any]:
    scope_total = row.get("scope_total")
    updated_count = int(row.get("updated_count") or 0)
    batch_limit = int(row.get("batch_limit") or 0)
    remaining_estimate = None
    progress_percent = None
    batches_remaining_estimate = None
    if scope_total is not None:
        scope_total = int(scope_total or 0)
        remaining_estimate = max(0, scope_total - updated_count)
        progress_percent = round((updated_count / scope_total) * 100, 1) if scope_total else None
        batches_remaining_estimate = ceil(remaining_estimate / batch_limit) if batch_limit else None
    return {
        "job_id": str(row.get("_id")),
        "schema_version": row.get("schema_version"),
        "status": row.get("status"),
        "batch_limit": batch_limit,
        "label": row.get("label"),
        "document_id": row.get("document_id"),
        "force": row.get("force", False),
        "scope_total": scope_total,
        "remaining_estimate": remaining_estimate,
        "progress_percent": progress_percent,
        "batches_remaining_estimate": batches_remaining_estimate,
        "created_by": row.get("created_by"),
        "batch_count": row.get("batch_count", 0),
        "updated_count": updated_count,
        "skipped_count": row.get("skipped_count", 0),
        "error_count": row.get("error_count", 0),
        "last_node_id": row.get("last_node_id"),
        "resume_after_node_id": row.get("last_node_id"),
        "transaction_boundary": row.get("transaction_boundary") or "batch_cursor_after_completed_batch",
        "node_write_boundary": row.get("node_write_boundary") or "individual_node_update",
        "interruption_recovery": interruption_recovery_summary(row),
        "reason": row.get("reason"),
        "requeued_at": row.get("requeued_at").isoformat() if row.get("requeued_at") else None,
        "requeued_by": row.get("requeued_by"),
        "requeue_reason": row.get("requeue_reason"),
        "last_result": row.get("last_result"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def interruption_recovery_summary(row: dict[str, Any]) -> str:
    if row.get("force"):
        return (
            "If interrupted during a batch, requeue the processing job. The job resumes from the last "
            "completed batch cursor; the interrupted batch may be rebuilt because force replace is enabled."
        )
    return (
        "If interrupted during a batch, requeue the processing job. The job resumes from the last "
        "completed batch cursor; nodes already given profiles are skipped on replay."
    )
