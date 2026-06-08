from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from tirzah.domains.registry import (
    DEFAULT_PROJECT_DOMAIN_ID,
    conversation_domain_id_for_session,
    ensure_conversation_domain,
)
from tirzah.sessions.active_documents import record_active_documents
from tirzah.sessions.output_ingestion import queue_exchange_output
from tirzah.sessions.registry import touch_session
from tirzah.sessions.usage import record_node_usage


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dedupe_ids(values: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def save_exchange(
    db: Database,
    query: str,
    answer: dict[str, Any],
    prompt: dict[str, Any],
    focus_node_id: str | None,
    session_id: str = "default",
    process_trace: list[dict[str, Any]] | None = None,
    project_domain_id: str | None = None,
    conversation_domain_id: str | None = None,
) -> str:
    now = utc_now()
    touch_session(db, session_id)
    project_domain_id = project_domain_id or DEFAULT_PROJECT_DOMAIN_ID
    conversation_domain_id = conversation_domain_id or conversation_domain_id_for_session(session_id)
    ensure_conversation_domain(
        db,
        domain_id=conversation_domain_id,
        project_domain_id=project_domain_id,
        session_id=session_id,
    )
    used_node_ids = [str(node_id) for node_id in answer.get("used_node_ids", [])]
    if focus_node_id:
        used_node_ids.append(focus_node_id)
    used_node_ids = dedupe_ids(used_node_ids)
    active_document_ids = record_active_documents(db, session_id, used_node_ids)
    result = db.exchanges.insert_one(
        {
            "schema_version": 1,
            "session_id": session_id,
            "project_domain_id": project_domain_id,
            "conversation_domain_id": conversation_domain_id,
            "focus_node_id": ObjectId(focus_node_id) if focus_node_id else None,
            "query": query,
            "answer": answer,
            "prompt_budget": prompt.get("budget", {}),
            "context_metadata": prompt.get("context_metadata", {}),
            "active_document_ids": active_document_ids,
            "scored_node_count": 0,
            "process_trace": process_trace or [],
            "created_at": now,
        }
    )
    exchange_id = str(result.inserted_id)
    scored_node_count = record_node_usage(db, used_node_ids)
    db.exchanges.update_one(
        {"_id": result.inserted_id},
        {"$set": {"scored_node_count": scored_node_count}},
    )
    output_job_id = queue_exchange_output(
        db,
        exchange_id=exchange_id,
        session_id=session_id,
        query=query,
        answer=answer,
        used_node_ids=used_node_ids,
        active_document_ids=active_document_ids,
    )
    if output_job_id:
        db.exchanges.update_one(
            {"_id": result.inserted_id},
            {"$set": {"output_ingestion_job_id": output_job_id}},
        )
    return exchange_id


def recent_exchanges(
    db: Database,
    limit: int = 10,
    session_id: str | None = None,
    query_text: str | None = None,
    adapter: str | None = None,
    model: str | None = None,
) -> list[dict]:
    query = exchange_filter_query(
        session_id=session_id,
        query_text=query_text,
        adapter=adapter,
        model=model,
    )
    return [
        serialize_exchange(row)
        for row in db.exchanges.find(query).sort("created_at", -1).limit(bounded_limit(limit))
    ]


def exchange_filter_query(
    session_id: str | None = None,
    query_text: str | None = None,
    adapter: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if session_id:
        query["session_id"] = session_id
    if adapter:
        query["answer.adapter"] = adapter
    if model:
        query["answer.model"] = model
    if query_text:
        pattern = re.compile(re.escape(query_text), re.IGNORECASE)
        query["$or"] = [{"query": pattern}, {"answer.answer": pattern}]
    return query


def bounded_limit(value: int, maximum: int = 100) -> int:
    return max(1, min(maximum, int(value)))


def serialize_exchange(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "exchange_id": str(row["_id"]),
        "session_id": row.get("session_id"),
        "project_domain_id": row.get("project_domain_id"),
        "conversation_domain_id": row.get("conversation_domain_id"),
        "focus_node_id": str(row["focus_node_id"]) if row.get("focus_node_id") else None,
        "query": row.get("query"),
        "answer": row.get("answer", {}).get("answer"),
        "adapter": row.get("answer", {}).get("adapter"),
        "model": row.get("answer", {}).get("model"),
        "used_node_ids": row.get("answer", {}).get("used_node_ids", []),
        "scored_node_count": row.get("scored_node_count", 0),
        "output_ingestion_job_id": row.get("output_ingestion_job_id"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }
