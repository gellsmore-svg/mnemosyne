from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.database import Database


def list_documents(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    documents = db.documents.find({}).sort("created_at", -1).limit(limit)
    return [serialize_document(document) for document in documents]


def get_document(db: Database, document_id: str) -> dict[str, Any] | None:
    document = db.documents.find_one({"_id": ObjectId(document_id)})
    if not document:
        return None
    serialized = serialize_document(document)
    serialized["tree_count"] = db.trees.count_documents({"document_id": document["_id"]})
    serialized["node_count"] = db.nodes.count_documents({"document_id": document["_id"]})
    return serialized


def search_nodes(
    db: Database,
    query: str | None = None,
    label: str | None = None,
    endorsement_label: str | None = None,
    document_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if query:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        filters["$or"] = [{"title": pattern}, {"text": pattern}]
    if label:
        filters["labels"] = label
    if endorsement_label:
        filters["endorsement_label"] = endorsement_label
    if document_id:
        filters["document_id"] = ObjectId(document_id)
    created_filter = {}
    if created_after:
        created_filter["$gte"] = created_after
    if created_before:
        created_filter["$lte"] = created_before
    if created_filter:
        filters["created_at"] = created_filter

    nodes = db.nodes.find(filters).sort("created_at", -1).limit(limit)
    return [serialize_node(node) for node in nodes]


def node_context(db: Database, node_id: str, child_limit: int = 20) -> dict[str, Any] | None:
    node = db.nodes.find_one({"_id": ObjectId(node_id)})
    if not node:
        return None
    parent = None
    if node.get("parent_id"):
        parent = db.nodes.find_one({"_id": node["parent_id"]})
    children = list(
        db.nodes.find({"parent_id": node["_id"]}).sort("order", 1).limit(child_limit)
    )
    document = db.documents.find_one({"_id": node["document_id"]})
    return {
        "document": serialize_document(document) if document else None,
        "node": serialize_node(node),
        "parent": serialize_node(parent) if parent else None,
        "children": [serialize_node(child) for child in children],
    }


def compile_context(
    db: Database,
    node_id: str,
    ancestor_depth: int = 3,
    sibling_window: int = 1,
    child_depth: int = 1,
    child_limit: int = 20,
) -> dict[str, Any] | None:
    node = db.nodes.find_one({"_id": ObjectId(node_id)})
    if not node:
        return None
    document = db.documents.find_one({"_id": node["document_id"]})
    records = [context_record("focus", node, distance=0)]

    current = node
    for distance in range(1, ancestor_depth + 1):
        parent_id = current.get("parent_id")
        if not parent_id:
            break
        parent = db.nodes.find_one({"_id": parent_id})
        if not parent:
            break
        records.append(context_record("ancestor", parent, distance=distance))
        current = parent

    if node.get("parent_id") and sibling_window > 0:
        siblings = nearby_siblings(db, node, sibling_window)
        records.extend(context_record("sibling", sibling, distance=1) for sibling in siblings)

    records.extend(
        context_record("descendant", child, distance=distance)
        for child, distance in descendants(db, node, max_depth=child_depth, limit=child_limit)
    )

    return {
        "document": serialize_document(document) if document else None,
        "focus_node_id": str(node["_id"]),
        "parameters": {
            "ancestor_depth": ancestor_depth,
            "sibling_window": sibling_window,
            "child_depth": child_depth,
            "child_limit": child_limit,
        },
        "records": records,
    }


def render_context_document(context: dict[str, Any], char_budget: int = 4000) -> dict[str, Any]:
    document = context.get("document") or {}
    header = [
        "# Mnemosyne Context",
        "",
        f"Document: {document.get('title') or '<unknown>'}",
        f"Document ID: {document.get('document_id') or '<unknown>'}",
        f"Focus Node ID: {context.get('focus_node_id')}",
        "",
        "## Context Records",
        "",
    ]
    parts = ["\n".join(header)]
    used_chars = len(parts[0])
    included = []
    skipped = []

    for record in prioritize_records(context.get("records", [])):
        block = render_record(record)
        if used_chars + len(block) > char_budget:
            skipped.append(
                {
                    "node_id": record["node_id"],
                    "role": record["role"],
                    "reason": "char_budget_exceeded",
                    "chars": len(block),
                }
            )
            continue
        parts.append(block)
        used_chars += len(block)
        included.append(
            {
                "node_id": record["node_id"],
                "role": record["role"],
                "distance": record["distance"],
                "chars": len(block),
            }
        )

    return {
        "text": "\n".join(parts).rstrip() + "\n",
        "char_budget": char_budget,
        "used_chars": used_chars,
        "estimated_tokens": estimate_tokens("\n".join(parts)),
        "included": included,
        "skipped": skipped,
    }


def build_prompt_envelope(
    context: dict[str, Any],
    query: str,
    system_instruction: str | None = None,
    token_budget: int = 2000,
    reserved_response_tokens: int = 500,
) -> dict[str, Any]:
    instruction = system_instruction or default_system_instruction()
    overhead_text = "\n".join(
        [
            instruction,
            "",
            "## User Query",
            query,
            "",
            "## Retrieved Context",
            "",
        ]
    )
    overhead_tokens = estimate_tokens(overhead_text)
    available_context_tokens = max(0, token_budget - reserved_response_tokens - overhead_tokens)
    char_budget = available_context_tokens * 4
    rendered = render_context_document(context, char_budget=char_budget)
    prompt_parts = [
        instruction,
        "",
        "## User Query",
        query,
        "",
        "## Retrieved Context",
        rendered["text"],
    ]
    prompt_text = "\n".join(prompt_parts).rstrip() + "\n"
    return {
        "system_instruction": instruction,
        "query": query,
        "context_text": rendered["text"],
        "prompt_text": prompt_text,
        "budget": {
            "token_budget": token_budget,
            "reserved_response_tokens": reserved_response_tokens,
            "estimated_overhead_tokens": overhead_tokens,
            "available_context_tokens": available_context_tokens,
            "estimated_prompt_tokens": estimate_tokens(prompt_text),
            "estimated_context_tokens": estimate_tokens(rendered["text"]),
            "estimated_total_with_reserved_response_tokens": estimate_tokens(prompt_text)
            + reserved_response_tokens,
        },
        "context_metadata": {
            "included": rendered["included"],
            "skipped": rendered["skipped"],
            "used_chars": rendered["used_chars"],
            "char_budget": rendered["char_budget"],
        },
    }


def build_prompt_envelope_without_context(
    query: str,
    system_instruction: str | None = None,
    token_budget: int = 2000,
    reserved_response_tokens: int = 500,
) -> dict[str, Any]:
    instruction = system_instruction or default_no_context_system_instruction()
    context_text = "\n".join(
        [
            "# Mnemosyne Context",
            "",
            "No retrieved Mongo context matched this request.",
            "",
            "## Submitted Prompt",
            query,
            "",
        ]
    )
    prompt_text = "\n".join(
        [
            instruction,
            "",
            "## User Query",
            query,
            "",
            "## Retrieved Context",
            context_text,
        ]
    ).rstrip() + "\n"
    return {
        "system_instruction": instruction,
        "query": query,
        "context_text": context_text,
        "prompt_text": prompt_text,
        "budget": {
            "token_budget": token_budget,
            "reserved_response_tokens": reserved_response_tokens,
            "estimated_overhead_tokens": estimate_tokens(instruction),
            "available_context_tokens": max(
                0, token_budget - reserved_response_tokens - estimate_tokens(instruction)
            ),
            "estimated_prompt_tokens": estimate_tokens(prompt_text),
            "estimated_context_tokens": estimate_tokens(context_text),
            "estimated_total_with_reserved_response_tokens": estimate_tokens(prompt_text)
            + reserved_response_tokens,
        },
        "context_metadata": {
            "included": [],
            "skipped": [],
            "used_chars": len(context_text),
            "char_budget": len(context_text),
            "retrieval_status": "no_focus_node",
        },
    }


def default_system_instruction() -> str:
    return (
        "Use the retrieved Mnemosyne context to answer the user query. "
        "Prefer explicitly endorsed material when present, preserve provenance, "
        "and say when the retrieved context is insufficient."
    )


def default_no_context_system_instruction() -> str:
    return (
        "Answer the user query directly. No retrieved Mongo context is available for this request, "
        "so do not imply that the answer comes from Mnemosyne memory. If the user asks about "
        "stored project knowledge, provenance, or what Mnemosyne has indexed, say that no "
        "matching Mongo context was retrieved before giving any general answer."
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def prioritize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    role_priority = {
        "focus": 0,
        "descendant": 1,
        "sibling": 2,
        "ancestor": 3,
    }
    return sorted(
        records,
        key=lambda record: (
            role_priority.get(record.get("role"), 9),
            record.get("distance", 99),
            record.get("title") or "",
        ),
    )


def render_record(record: dict[str, Any]) -> str:
    labels = ", ".join(record.get("labels", [])) or "<none>"
    provenance = record.get("provenance", {})
    lines = [
        f"### {record['role']} d={record['distance']}: {record.get('title') or '<untitled>'}",
        "",
        f"- Node ID: {record['node_id']}",
        f"- Labels: {labels}",
        f"- Endorsement: {record.get('endorsement_label') or '<none>'}",
        f"- Source: {provenance.get('archive_path') or provenance.get('source_path') or '<unknown>'}",
        "",
        record.get("text_preview") or "",
        "",
    ]
    return "\n".join(lines)


def descendants(
    db: Database,
    node: dict[str, Any],
    max_depth: int,
    limit: int,
) -> list[tuple[dict[str, Any], int]]:
    if max_depth <= 0 or limit <= 0:
        return []
    results: list[tuple[dict[str, Any], int]] = []
    frontier: list[tuple[dict[str, Any], int]] = [(node, 0)]
    while frontier and len(results) < limit:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        children = list(db.nodes.find({"parent_id": current["_id"]}).sort("order", 1))
        for child in children:
            if len(results) >= limit:
                break
            next_depth = depth + 1
            results.append((child, next_depth))
            frontier.append((child, next_depth))
    return results


def nearby_siblings(db: Database, node: dict[str, Any], window: int) -> list[dict[str, Any]]:
    siblings = list(db.nodes.find({"parent_id": node["parent_id"]}).sort("order", 1))
    focus_index = next(
        (index for index, sibling in enumerate(siblings) if sibling["_id"] == node["_id"]),
        None,
    )
    if focus_index is None:
        return []
    start = max(0, focus_index - window)
    end = focus_index + window + 1
    return [sibling for sibling in siblings[start:end] if sibling["_id"] != node["_id"]]


def context_record(role: str, node: dict[str, Any], distance: int) -> dict[str, Any]:
    serialized = serialize_node(node)
    serialized["role"] = role
    serialized["distance"] = distance
    return serialized


def serialize_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(document["_id"]),
        "schema_version": document.get("schema_version"),
        "title": document.get("title"),
        "summary": document.get("summary"),
        "source": document.get("source", {}),
        "created_at": iso(document.get("created_at")),
        "updated_at": iso(document.get("updated_at")),
    }


def serialize_node(node: dict[str, Any]) -> dict[str, Any]:
    text = node.get("text", "")
    return {
        "node_id": str(node["_id"]),
        "document_id": str(node["document_id"]),
        "tree_id": str(node["tree_id"]),
        "parent_id": str(node["parent_id"]) if node.get("parent_id") else None,
        "node_key": node.get("node_key"),
        "parent_key": node.get("parent_key"),
        "title": node.get("title"),
        "text_preview": text[:300],
        "labels": node.get("labels", []),
        "endorsement_label": node.get("endorsement_label"),
        "provenance": node.get("provenance", {}),
        "created_at": iso(node.get("created_at")),
    }


def iso(value: Any) -> str | None:
    if not value:
        return None
    return value.isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)
