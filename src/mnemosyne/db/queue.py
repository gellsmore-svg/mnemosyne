from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_source(db: Database, path: Path, checksum: str) -> dict[str, Any]:
    existing_document = db.documents.find_one({"source.checksum_sha256": checksum})
    existing_job = db.queue.find_one(
        {
            "checksum_sha256": checksum,
            "status": {"$in": ["pending", "processing", "completed"]},
        }
    )
    now = utc_now()
    status = "rejected" if existing_document or existing_job else "pending"
    reason = None
    if existing_document:
        reason = "duplicate_checksum"
    elif existing_job:
        reason = "duplicate_queued_checksum"

    job = {
        "path": str(path),
        "checksum_sha256": checksum,
        "status": status,
        "reason": reason,
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
    }
    if existing_document:
        job["existing_document_id"] = existing_document["_id"]
    if existing_job:
        job["existing_queue_id"] = existing_job["_id"]

    result = db.queue.insert_one(job)
    job["_id"] = result.inserted_id
    return job


def claim_next_pending(db: Database) -> dict[str, Any] | None:
    now = utc_now()
    return db.queue.find_one_and_update(
        {"status": "pending"},
        {
            "$set": {"status": "processing", "updated_at": now},
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def complete_job(db: Database, job_id: object, result: dict[str, Any]) -> None:
    db.queue.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "completed",
                "result": result,
                "updated_at": utc_now(),
            }
        },
    )


def reject_job(db: Database, job_id: object, reason: str, details: dict[str, Any]) -> None:
    db.queue.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "rejected",
                "reason": reason,
                "details": details,
                "updated_at": utc_now(),
            }
        },
    )


def fail_job(db: Database, job_id: object, reason: str, details: dict[str, Any]) -> None:
    db.queue.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "failed",
                "reason": reason,
                "details": details,
                "updated_at": utc_now(),
            }
        },
    )


def retry_job(db: Database, job_id: object, reason: str, details: dict[str, Any]) -> None:
    db.queue.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "pending",
                "reason": reason,
                "details": details,
                "updated_at": utc_now(),
            }
        },
    )


def queue_summary(db: Database) -> dict[str, Any]:
    statuses = {
        row["_id"]: row["count"]
        for row in db.queue.aggregate(
            [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        )
    }
    oldest_pending = db.queue.find_one(
        {"status": "pending"},
        sort=[("created_at", 1)],
        projection={"_id": 1, "path": 1, "created_at": 1, "attempts": 1},
    )
    return {
        "total": sum(statuses.values()),
        "statuses": statuses,
        "oldest_pending": oldest_pending,
    }


def recent_jobs(db: Database, limit: int = 10, status: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    return list(
        db.queue.find(query)
        .sort("updated_at", -1)
        .limit(limit)
    )
