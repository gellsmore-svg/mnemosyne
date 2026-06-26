"""Semantic chunking of conversation turns (Phase 3).

Decomposes a turn (user prompt + assistant answer) into typed semantic chunks
(topic / intent / domain / requirement / entity / decision) for finer-grained
memory. Best-effort, runs async + durable like turn embedding, gated by
``conversation_chunking``. Relationship mapping and chunk-level retrieval are the
next slices; this establishes the extraction + storage foundation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo.database import Database

CHUNK_COLLECTION = "conversation_chunks"
CHUNK_REL_COLLECTION = "chunk_relationships"
CHUNK_KINDS = (
    "topic",
    "intent",
    "domain",
    "requirement",
    "entity",
    "decision",
    "process",
    "unresolved",
    "assumption",
    "constraint",
)
# The process/decision/unresolved taxonomy (Phase 5): what was decided, assumed,
# constrained, planned, and what is still open.
TAXONOMY_KINDS = ("decision", "assumption", "constraint", "process", "unresolved")
_MAX_CHUNKS = 12
_MAX_TEXT = 240


def _extract_json(text: str) -> Any:
    if not isinstance(text, str):
        return None
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                continue
    return None


def build_chunk_prompt(query: str, answer: str) -> str:
    return (
        "Decompose this conversation turn into a few SEMANTIC CHUNKS. Each chunk has a "
        "kind (one of: topic, intent, domain, requirement, entity, decision, process, "
        "unresolved, assumption, constraint) and a short text. Use 'decision' for choices "
        "made, 'unresolved' for open questions / deferred work, 'assumption' and "
        "'constraint' where relevant, 'process' for plan/next-step statements. "
        'Return ONLY JSON: {"chunks": [{"kind": "topic", "text": "..."}, ...]}. '
        "Keep chunks atomic and concise.\n\n"
        f"User: {query}\nAssistant: {answer}\n"
    )


def parse_chunks(text: str) -> list[dict[str, Any]]:
    """Parse the chunker's JSON output into validated {kind, text} chunks."""
    payload = _extract_json(text)
    if isinstance(payload, dict):
        items = payload.get("chunks")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("chunker did not return a chunk list")
    chunks: list[dict[str, Any]] = []
    for item in items[:_MAX_CHUNKS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "topic").strip().lower()
        if kind not in CHUNK_KINDS:
            kind = "topic"
        body = str(item.get("text") or "").strip()[:_MAX_TEXT]
        if body:
            chunks.append({"kind": kind, "text": body})
    if not chunks:
        raise ValueError("no valid chunks parsed")
    return chunks


def heuristic_chunks(query: str, answer: str) -> list[dict[str, Any]]:
    """Deterministic fallback: the user's question as a single topic chunk."""
    text = (query or "").strip()[:_MAX_TEXT]
    return [{"kind": "topic", "text": text}] if text else []


def make_chunker(adapter: Any):
    """Wrap an answer adapter into a chunker(query, answer) -> chunks."""

    def chunker(query: str, answer: str) -> list[dict[str, Any]]:
        try:
            result = adapter.answer({"prompt_text": build_chunk_prompt(query, answer)})
            text = result.get("answer", "") if isinstance(result, dict) else str(result)
            return parse_chunks(text)
        except Exception:
            return heuristic_chunks(query, answer)

    return chunker


def store_chunks(db: Database, *, exchange_id: str, session_id: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist chunks for a turn and mark the exchange chunked (best-effort).

    Each chunk may carry an ``embedding`` (for chunk-level retrieval). Returns the
    stored rows (with chunk_id) so the caller can link relationships.
    """
    now = datetime.now(timezone.utc)
    rows = [
        {
            "chunk_id": f"chunk_{uuid4().hex}",
            "exchange_id": exchange_id,
            "session_id": session_id,
            "kind": chunk["kind"],
            "text": chunk["text"],
            "embedding": chunk.get("embedding"),
            "created_at": now,
        }
        for chunk in chunks
    ]
    if rows:
        try:
            db[CHUNK_COLLECTION].insert_many([dict(row) for row in rows])
        except Exception:
            pass
    try:
        db.exchanges.update_one({"_id": ObjectId(exchange_id)}, {"$set": {"chunked": True}})
    except Exception:
        pass
    return rows


def relevant_chunks(
    db: Database,
    *,
    session_id: str,
    query_vector: list[float] | None,
    limit: int = 5,
    exclude_exchange_ids: Any = (),
    kinds: Any = None,
) -> list[dict[str, Any]]:
    """Top-K chunks of the session most similar to ``query_vector`` (cosine).

    Finer-grained recall than whole turns. Only chunks with an embedding count.
    ``kinds`` optionally restricts to specific taxonomy kinds. Best-effort.
    """
    if not query_vector:
        return []
    from tirzah.retrieval.queries import cosine_similarity

    exclude = set(exclude_exchange_ids or ())
    kind_filter = set(kinds) if kinds else None
    try:
        rows = list(db[CHUNK_COLLECTION].find({"session_id": session_id}))
    except Exception:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if row.get("exchange_id") in exclude:
            continue
        if kind_filter and row.get("kind") not in kind_filter:
            continue
        vector = row.get("embedding")
        if not vector:
            continue
        try:
            scored.append((cosine_similarity(query_vector, vector), row))
        except Exception:
            continue
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"kind": row.get("kind"), "text": row.get("text"), "exchange_id": row.get("exchange_id")}
        for _, row in scored[:limit]
    ]


def link_chunk_similarities(
    db: Database, new_rows: list[dict[str, Any]], *, session_id: str, k: int = 3, threshold: float = 0.6
) -> int:
    """Create similar_to relationships from each new chunk to existing session chunks.

    Embedding-cosine similarity (cheap, deterministic). The richer LLM relationships
    (extends/clarifies/contradicts) are a later slice. Best-effort.
    """
    embedded_new = [row for row in new_rows if row.get("embedding")]
    if not embedded_new:
        return 0
    from tirzah.retrieval.queries import cosine_similarity

    try:
        existing = [
            row
            for row in db[CHUNK_COLLECTION].find({"session_id": session_id})
            if row.get("embedding")
        ]
    except Exception:
        return 0
    new_ids = {row["chunk_id"] for row in embedded_new}
    now = datetime.now(timezone.utc)
    edges: list[dict[str, Any]] = []
    for row in embedded_new:
        scored = []
        for other in existing:
            if other.get("chunk_id") in new_ids:
                continue  # don't link to this turn's own new chunks
            try:
                score = cosine_similarity(row["embedding"], other["embedding"])
            except Exception:
                continue
            if score >= threshold:
                scored.append((score, other))
        scored.sort(key=lambda item: item[0], reverse=True)
        for score, other in scored[:k]:
            edges.append(
                {
                    "kind": "similar_to",
                    "source_chunk_id": row["chunk_id"],
                    "target_chunk_id": other["chunk_id"],
                    "session_id": session_id,
                    "score": round(float(score), 4),
                    "created_at": now,
                }
            )
    if edges:
        try:
            db[CHUNK_REL_COLLECTION].insert_many(edges)
        except Exception:
            pass
    return len(edges)


def pending_chunk_exchanges(db: Database, *, limit: int = 200) -> list[dict[str, Any]]:
    """Exchanges not yet chunked — the durable pending-chunking queue."""
    capped = max(1, min(int(limit), 1000))
    try:
        rows = list(db.exchanges.find({"chunked": {"$ne": True}}).sort("created_at", -1).limit(capped))
    except Exception:
        return []
    pending = []
    for row in rows:
        query = row.get("query")
        answer = row.get("answer")
        if isinstance(answer, dict):
            answer = answer.get("answer")
        if query:
            pending.append(
                {
                    "exchange_id": str(row["_id"]),
                    "session_id": row.get("session_id"),
                    "query": query,
                    "answer": answer or "",
                }
            )
    return pending


def list_chunks(db: Database, *, session_id: str | None = None, exchange_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if session_id:
        query["session_id"] = session_id
    if exchange_id:
        query["exchange_id"] = exchange_id
    try:
        return list(db[CHUNK_COLLECTION].find(query, {"_id": 0}).limit(max(1, int(limit))))
    except Exception:
        return []
