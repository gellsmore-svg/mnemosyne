from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database

from mnemosyne.db.repositories import backfill_node_embeddings, bounded_candidate_limit


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
    job = {
        "schema_version": EMBEDDING_BACKFILL_SCHEMA_VERSION,
        "status": "pending",
        "batch_limit": bounded_candidate_limit(batch_limit, maximum=1000),
        "label": str(label).strip() if label else None,
        "document_id": str(document_id).strip() if document_id else None,
        "force": bool(force),
        "created_by": created_by,
        "batch_count": 0,
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "last_node_id": None,
        "last_result": None,
        "reason": None,
        "created_at": now,
        "updated_at": now,
    }
    result = db.embedding_backfill_jobs.insert_one(job)
    job["_id"] = result.inserted_id
    return serialize_embedding_backfill_job(job)


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
    return {
        "job_id": str(row.get("_id")),
        "schema_version": row.get("schema_version"),
        "status": row.get("status"),
        "batch_limit": row.get("batch_limit"),
        "label": row.get("label"),
        "document_id": row.get("document_id"),
        "force": row.get("force", False),
        "created_by": row.get("created_by"),
        "batch_count": row.get("batch_count", 0),
        "updated_count": row.get("updated_count", 0),
        "skipped_count": row.get("skipped_count", 0),
        "error_count": row.get("error_count", 0),
        "last_node_id": row.get("last_node_id"),
        "reason": row.get("reason"),
        "last_result": row.get("last_result"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }
