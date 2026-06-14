from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from tirzah.db.schema import collection_available
from tirzah.db.repositories import DuplicateSourceError, commit_ingestion, summarize_node_text
from tirzah.models.ingestion import (
    DEFAULT_ENDORSEMENT_LABEL,
    IngestedNode,
    IngestionResult,
    SourceRef,
)
from tirzah.sessions.active_documents import record_active_documents


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
    if not collection_available(db, "output_ingestion_queue"):
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
    try:
        result = db.output_ingestion_queue.insert_one(job)
        return str(result.inserted_id)
    except DuplicateKeyError:
        existing = db.output_ingestion_queue.find_one({"exchange_id": exchange_id})
        if existing:
            return str(existing["_id"])
        raise


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
    if not collection_available(db, "output_ingestion_queue"):
        return []
    rows = (
        db.output_ingestion_queue.find(
            output_ingestion_filter_query(status=status, session_id=session_id)
        )
        .sort("created_at", -1)
        .limit(bounded_limit(limit))
    )
    return [serialize_output_ingestion_job(row) for row in rows]


def claim_next_output_job(
    db: Database,
    session_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    if not collection_available(db, "output_ingestion_queue"):
        return None
    now = utc_now()
    query: dict[str, Any] = {"status": "pending"}
    if job_id:
        query["_id"] = ObjectId(job_id)
    elif session_id:
        query["session_id"] = session_id
    return db.output_ingestion_queue.find_one_and_update(
        query,
        {
            "$set": {"status": "processing", "updated_at": now},
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def process_next_output_ingestion(
    db: Database,
    session_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    job = claim_next_output_job(db, session_id=session_id, job_id=job_id)
    if not job:
        result = {"ok": True, "status": "idle"}
        if session_id:
            result["session_id"] = session_id
        if job_id:
            result["job_id"] = job_id
        return result

    try:
        result = commit_output_job(db, job)
    except DuplicateSourceError as error:
        result = {
            "status": "rejected",
            "reason": "duplicate_output_checksum",
            "existing_document_id": str(error.existing_document_id),
            "checksum_sha256": error.checksum,
        }
        reject_output_job(db, job["_id"], "duplicate_output_checksum", result)
        return {"ok": False, "job_id": str(job["_id"]), **result}
    except Exception as error:
        fail_output_job(db, job["_id"], "output_ingestion_failed", {"error": str(error)})
        return {
            "ok": False,
            "status": "failed",
            "job_id": str(job["_id"]),
            "reason": "output_ingestion_failed",
            "error": str(error),
        }

    complete_output_job(db, job["_id"], result)
    db.exchanges.update_one(
        {"_id": parse_exchange_id(job.get("exchange_id"))},
        {
            "$set": {
                "output_document_id": result["document_id"],
                "output_tree_id": result["tree_id"],
                "output_node_ids": result["node_ids"],
            }
        },
    )
    active_document_ids = record_active_documents(
        db,
        session_id=str(job.get("session_id") or "default"),
        node_ids=result["node_ids"],
    )
    return {
        "ok": True,
        "status": "completed",
        "job_id": str(job["_id"]),
        "active_document_ids": active_document_ids,
        **result,
    }


def commit_output_job(db: Database, job: dict[str, Any]) -> dict[str, Any]:
    result = output_job_to_ingestion_result(job)
    return commit_ingestion(db, result)


def output_job_to_ingestion_result(job: dict[str, Any]) -> IngestionResult:
    answer_text = str(job.get("answer_text") or "").strip()
    query = str(job.get("query") or "").strip()
    exchange_id = str(job.get("exchange_id") or "unknown")
    title = output_document_title(query=query, exchange_id=exchange_id)
    source_path = f"tirzah://exchange/{exchange_id}/answer"
    checksum = str(
        job.get("content_hash_sha256")
        or output_content_hash(
            str(job.get("session_id") or "default"),
            query,
            answer_text,
        )
    )
    metadata = {
        "source_type": job.get("source_type"),
        "exchange_id": exchange_id,
        "session_id": job.get("session_id"),
        "query": query,
        "answer_adapter": job.get("answer_adapter"),
        "answer_model": job.get("answer_model"),
        "used_node_ids": job.get("used_node_ids", []),
        "active_document_ids": job.get("active_document_ids", []),
    }
    return IngestionResult(
        source=SourceRef(
            path=source_path,
            kind="llm_answer",
            checksum_sha256=checksum,
            archive_path=None,
        ),
        title=title,
        summary=summarize_node_text(answer_text),
        tree_label="llm_output",
        adapter="output_ingestion",
        nodes=[
            IngestedNode(
                node_key="root",
                title=title,
                text=f"# {title}\n\n{answer_text}",
                summary=summarize_node_text(answer_text),
                labels=["generated_output", "llm_answer", "source_root"],
                endorsement_label=DEFAULT_ENDORSEMENT_LABEL,
                metadata=metadata,
            ),
            IngestedNode(
                node_key="answer",
                parent_key="root",
                title="Answer",
                text=answer_text,
                summary=summarize_node_text(answer_text),
                labels=["generated_output", "llm_answer", "source_chunk"],
                endorsement_label=DEFAULT_ENDORSEMENT_LABEL,
                metadata=metadata,
            ),
        ],
        created_at=job.get("created_at") or utc_now(),
    )


def output_document_title(query: str, exchange_id: str) -> str:
    cleaned = " ".join(query.split())
    if cleaned:
        return f"LLM Answer: {cleaned[:80]}"
    return f"LLM Answer: {exchange_id}"


def complete_output_job(db: Database, job_id: object, result: dict[str, Any]) -> None:
    db.output_ingestion_queue.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "completed",
                "result": result,
                "updated_at": utc_now(),
            }
        },
    )


def reject_output_job(db: Database, job_id: object, reason: str, details: dict[str, Any]) -> None:
    db.output_ingestion_queue.update_one(
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


def fail_output_job(db: Database, job_id: object, reason: str, details: dict[str, Any]) -> None:
    db.output_ingestion_queue.update_one(
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


def parse_exchange_id(value: Any) -> Any:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return value


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
        "reason": row.get("reason"),
        "result": row.get("result"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }
