from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database


SEARCH_STOPWORDS = {
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "does",
    "did",
    "was",
    "were",
    "are",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
}
STRUCTURAL_LABELS = {
    "source_root",
    "source_section",
    "source_chunk",
}


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
    identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if query:
        filters["$or"] = text_query_filters(query)
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

    candidate_limit = max(limit * 5, 50) if query else limit
    if identity:
        candidate_limit = max(candidate_limit * 2, limit * 10, 50)
    nodes = list(db.nodes.find(filters).sort("created_at", -1).limit(candidate_limit))
    if identity:
        nodes = filter_nodes_for_identity(nodes, identity)
    if query:
        nodes.sort(key=lambda node: node_search_sort_key(node, query), reverse=True)
    return [serialize_node(node) for node in nodes[:limit]]


def filter_nodes_for_identity(
    nodes: list[dict[str, Any]],
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    return [node for node in nodes if node_visible_to_identity(node, identity)]


def node_visible_to_identity(node: dict[str, Any], identity: dict[str, Any]) -> bool:
    excluded_labels = set(identity.get("excluded_labels") or [])
    if excluded_labels and excluded_labels.intersection(node.get("labels") or []):
        return False
    excluded_document_ids = set(str(value) for value in identity.get("excluded_document_ids") or [])
    if excluded_document_ids and str(node.get("document_id")) in excluded_document_ids:
        return False
    excluded_tree_ids = set(str(value) for value in identity.get("excluded_tree_ids") or [])
    if excluded_tree_ids and str(node.get("tree_id")) in excluded_tree_ids:
        return False
    return True


def text_query_filters(query: str) -> list[dict[str, Any]]:
    exact_pattern = re.compile(re.escape(query), re.IGNORECASE)
    filters = [{"title": exact_pattern}, {"text": exact_pattern}, {"labels": exact_pattern}]
    exact_key = query.strip().lower()
    for term in text_query_terms(query):
        if term.lower() == exact_key:
            continue
        term_pattern = text_term_pattern(term)
        filters.extend([{"title": term_pattern}, {"text": term_pattern}, {"labels": term_pattern}])
    return filters


def text_term_pattern(term: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)


def text_query_terms(query: str) -> list[str]:
    terms = []
    seen = set()
    for term in re.findall(r"[A-Za-z0-9]+", query):
        key = term.lower()
        if len(key) < 3 or key in SEARCH_STOPWORDS or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms[:8]


def node_search_score(node: dict[str, Any], query: str) -> int:
    title = str(node.get("title") or "").lower()
    text = str(node.get("text") or "").lower()
    labels = node.get("labels", [])
    query_lower = query.lower()
    terms = [term.lower() for term in text_query_terms(query)]
    score = 0
    if query_lower in title:
        score += 50
    if query_lower in text:
        score += 15
    for term in terms:
        pattern = text_term_pattern(term)
        if pattern.search(title):
            score += 8
        if pattern.search(text):
            score += 2
    if "source_section" in labels:
        score += 4
    if "source_chunk" in labels:
        score += 5
    if "source_root" in labels:
        score -= 12
    if "source_section" in labels and len(text) > 2500 and query_lower not in title:
        score -= 20
    if not str(node.get("text") or "").strip():
        score -= 25
    endorsement_label = node.get("endorsement_label")
    if endorsement_label == "explicit_endorsed":
        score += 30
    elif endorsement_label == "implicit_endorsed":
        score += 10
    elif endorsement_label == "rejected":
        score -= 100
    if "generated_output" in labels and endorsement_label == "unreviewed":
        score -= 15
    score += usage_score_bonus(node.get("usage_score"))
    return score


def node_search_sort_key(node: dict[str, Any], query: str) -> tuple[int, float, int]:
    return (
        node_search_score(node, query),
        datetime_sort_value(node.get("last_used_at")),
        -len(str(node.get("text") or "")),
    )


def datetime_sort_value(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return 0.0


def parsed_usage_score(value: Any) -> int:
    try:
        usage_score = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(usage_score, 0)


def usage_score_bonus(value: Any) -> int:
    return min(parsed_usage_score(value), 10)


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


def semantic_candidate_nodes(
    db: Database,
    node_id: str,
    limit: int = 10,
    include_same_document: bool = False,
) -> list[dict[str, Any]]:
    node_object_id = parse_object_id(node_id)
    if not node_object_id:
        return []
    focus = db.nodes.find_one({"_id": node_object_id})
    if not focus:
        return []
    labels = semantic_labels(focus.get("labels") or [])
    if not labels:
        return []
    filters: dict[str, Any] = {
        "_id": {"$ne": focus["_id"]},
        "labels": {"$in": labels},
    }
    if not include_same_document and focus.get("document_id"):
        filters["document_id"] = {"$ne": focus["document_id"]}
    candidate_limit = max(limit * 5, 50)
    candidates = [
        candidate
        for candidate in db.nodes.find(filters).limit(candidate_limit)
        if "source_root" not in (candidate.get("labels") or [])
    ]
    candidates.sort(key=lambda candidate: semantic_candidate_sort_tuple(candidate, labels))
    return [
        {
            **serialize_node(candidate),
            "shared_labels": sorted(set(candidate.get("labels") or []) & set(labels)),
            "shared_label_count": len(set(candidate.get("labels") or []) & set(labels)),
        }
        for candidate in candidates[:limit]
    ]


def semantic_labels(labels: list[str]) -> list[str]:
    return sorted(
        {
            label
            for label in labels
            if label
            and label not in STRUCTURAL_LABELS
            and not label.startswith("source_")
        }
    )


def semantic_candidate_sort_tuple(
    candidate: dict[str, Any],
    focus_labels: list[str],
) -> tuple[int, int, tuple[tuple[int, Any], ...]]:
    candidate_labels = set(candidate.get("labels") or [])
    shared_count = len(candidate_labels & set(focus_labels))
    return (
        -shared_count,
        -usage_score_bonus(candidate.get("usage_score")),
        natural_sort_key(str(candidate.get("title") or candidate.get("_id") or "")),
    )


def graph_edges_for_node(
    db: Database,
    node_id: str,
    direction: str = "both",
    relation_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not hasattr(db, "graph_edges"):
        return []
    node_object_id = parse_object_id(node_id)
    if not node_object_id:
        return []
    filters: dict[str, Any] = edge_direction_filter(node_object_id, direction)
    if relation_type:
        filters["relation_type"] = relation_type
    edges = list(db.graph_edges.find(filters).sort("created_at", -1).limit(limit))
    return [serialize_graph_edge(db, edge) for edge in edges]


def expand_proximity(
    db: Database,
    node_id: str,
    direction: str = "both",
    relation_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    node_object_id = parse_object_id(node_id)
    if not node_object_id:
        return []
    candidates = []
    for edge in graph_edges_for_node(
        db,
        node_id=node_id,
        direction=direction,
        relation_type=relation_type,
        limit=max(limit * 4, 20),
    ):
        adjacent = adjacent_node_for_edge(edge, node_id)
        if not adjacent:
            continue
        candidates.append(
            {
                "node_id": adjacent.get("node_id"),
                "title": adjacent.get("title"),
                "text_preview": adjacent.get("text_preview"),
                "labels": adjacent.get("labels") or [],
                "endorsement_label": adjacent.get("endorsement_label"),
                "edge": edge_summary(edge),
                "proximity_score": edge_proximity_score(edge),
            }
        )
    candidates.sort(
        key=lambda item: (
            item.get("proximity_score") or 0.0,
            item.get("title") or "",
        ),
        reverse=True,
    )
    return candidates[:limit]


def expand_graph_paths(
    db: Database,
    node_id: str,
    direction: str = "both",
    relation_type: str | None = None,
    max_depth: int = 2,
    limit: int = 10,
    branch_limit: int = 5,
) -> list[dict[str, Any]]:
    node_object_id = parse_object_id(node_id)
    if not node_object_id:
        return []
    max_depth = bounded_int(max_depth, default=2, minimum=1, maximum=3)
    limit = bounded_int(limit, default=10, minimum=1, maximum=20)
    branch_limit = bounded_int(branch_limit, default=5, minimum=1, maximum=10)
    frontier = [
        {
            "node_id": node_id,
            "path_score": 1.0,
            "path_edges": [],
            "visited": {node_id},
        }
    ]
    best_by_node: dict[str, dict[str, Any]] = {}
    for depth in range(1, max_depth + 1):
        next_frontier = []
        for path in frontier:
            for edge in sorted_path_edges(
                db,
                node_id=path["node_id"],
                direction=direction,
                relation_type=relation_type,
                branch_limit=branch_limit,
            ):
                adjacent = adjacent_node_for_edge(edge, path["node_id"])
                adjacent_id = str(adjacent.get("node_id") or "") if adjacent else ""
                if not adjacent_id or adjacent_id in path["visited"]:
                    continue
                path_edges = [*path["path_edges"], edge_summary(edge)]
                path_score = round(path["path_score"] * edge_proximity_score(edge), 6)
                candidate = {
                    "node_id": adjacent_id,
                    "title": adjacent.get("title"),
                    "text_preview": adjacent.get("text_preview"),
                    "labels": adjacent.get("labels") or [],
                    "endorsement_label": adjacent.get("endorsement_label"),
                    "path_depth": depth,
                    "path_score": path_score,
                    "path_edges": path_edges,
                }
                previous = best_by_node.get(adjacent_id)
                if previous is None or graph_path_quality_tuple(candidate) > graph_path_quality_tuple(previous):
                    best_by_node[adjacent_id] = candidate
                next_frontier.append(
                    {
                        "node_id": adjacent_id,
                        "path_score": path_score,
                        "path_edges": path_edges,
                        "visited": {*path["visited"], adjacent_id},
                    }
                )
        frontier = sorted(
            next_frontier,
            key=frontier_sort_tuple,
        )[:branch_limit]
        if not frontier:
            break
    candidates = sorted(best_by_node.values(), key=graph_path_output_sort_tuple)
    return candidates[:limit]


def sorted_path_edges(
    db: Database,
    node_id: str,
    direction: str,
    relation_type: str | None,
    branch_limit: int,
) -> list[dict[str, Any]]:
    edges = graph_edges_for_node(
        db,
        node_id=node_id,
        direction=direction,
        relation_type=relation_type,
        limit=max(branch_limit * 4, 20),
    )
    edges.sort(key=lambda edge: edge_path_sort_tuple(edge, node_id))
    return edges[:branch_limit]


def edge_path_sort_tuple(edge: dict[str, Any], node_id: str) -> tuple[float, tuple[tuple[int, Any], ...]]:
    adjacent = adjacent_node_for_edge(edge, node_id) or {}
    adjacent_key = (
        adjacent.get("node_key")
        or adjacent.get("title")
        or adjacent.get("node_id")
        or edge.get("target_node_id")
        or edge.get("source_node_id")
        or ""
    )
    return (-edge_proximity_score(edge), natural_sort_key(str(adjacent_key)))


def frontier_sort_tuple(path: dict[str, Any]) -> tuple[float, tuple[tuple[int, Any], ...]]:
    return (-float(path.get("path_score") or 0.0), natural_sort_key(str(path.get("node_id") or "")))


def graph_path_quality_tuple(path: dict[str, Any]) -> tuple[float, int]:
    return (
        float(path.get("path_score") or 0.0),
        -int(path.get("path_depth") or 0),
    )


def graph_path_output_sort_tuple(path: dict[str, Any]) -> tuple[float, int, tuple[tuple[int, Any], ...]]:
    return (
        -float(path.get("path_score") or 0.0),
        int(path.get("path_depth") or 0),
        natural_sort_key(str(path.get("title") or path.get("node_id") or "")),
    )


def natural_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts = []
    for part in re.split(r"(\d+)", value):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.lower()))
    return tuple(parts)


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def adjacent_node_for_edge(edge: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if edge.get("source_node_id") == node_id:
        return edge.get("target_node")
    if edge.get("target_node_id") == node_id:
        return edge.get("source_node")
    return None


def edge_summary(edge: dict[str, Any]) -> dict[str, Any]:
    provenance = edge.get("provenance") or {}
    return {
        "edge_id": edge.get("edge_id"),
        "source_node_id": edge.get("source_node_id"),
        "target_node_id": edge.get("target_node_id"),
        "relation_type": edge.get("relation_type"),
        "weight": edge.get("weight"),
        "confidence": edge.get("confidence"),
        "direction": edge.get("direction"),
        "provenance_source": provenance.get("source"),
        "reviewer": provenance.get("reviewer"),
        "shared_label_count": provenance.get("shared_label_count"),
    }


def edge_proximity_score(edge: dict[str, Any]) -> float:
    return round(edge_numeric_value(edge.get("weight")) * edge_numeric_value(edge.get("confidence")), 4)


def edge_numeric_value(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def edge_direction_filter(node_id: ObjectId, direction: str) -> dict[str, Any]:
    if direction == "outgoing":
        return {"source_node_id": node_id}
    if direction == "incoming":
        return {"target_node_id": node_id}
    return {"$or": [{"source_node_id": node_id}, {"target_node_id": node_id}]}


def serialize_graph_edge(db: Database, edge: dict[str, Any]) -> dict[str, Any]:
    source_node = db.nodes.find_one({"_id": edge.get("source_node_id")})
    target_node = db.nodes.find_one({"_id": edge.get("target_node_id")})
    return {
        "edge_id": str(edge["_id"]) if edge.get("_id") else None,
        "schema_version": edge.get("schema_version"),
        "document_id": str(edge["document_id"]) if edge.get("document_id") else None,
        "tree_id": str(edge["tree_id"]) if edge.get("tree_id") else None,
        "source_node_id": str(edge["source_node_id"]) if edge.get("source_node_id") else None,
        "target_node_id": str(edge["target_node_id"]) if edge.get("target_node_id") else None,
        "source_node_key": edge.get("source_node_key"),
        "target_node_key": edge.get("target_node_key"),
        "relation_type": edge.get("relation_type"),
        "weight": edge.get("weight"),
        "confidence": edge.get("confidence"),
        "direction": edge.get("direction"),
        "provenance": edge.get("provenance") or {},
        "source_node": serialize_node(source_node) if source_node else None,
        "target_node": serialize_node(target_node) if target_node else None,
        "created_at": iso(edge.get("created_at")),
        "updated_at": iso(edge.get("updated_at")),
    }


def parse_object_id(value: Any) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


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


def render_context_document(
    context: dict[str, Any],
    char_budget: int = 4000,
    heading_level: int = 1,
) -> dict[str, Any]:
    document = context.get("document") or {}
    title_marker = "#" * max(1, heading_level)
    section_marker = "#" * max(1, heading_level + 1)
    header = [
        f"{title_marker} Mnemosyne Context",
        "",
        f"Document: {document.get('title') or '<unknown>'}",
        f"Document ID: {document.get('document_id') or '<unknown>'}",
        f"Focus Node ID: {context.get('focus_node_id')}",
        "",
        f"{section_marker} Context Records",
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
            "## Runtime Facts",
            "- Mongo context lookup: ran",
            "- Matching Mongo context used: no",
            "- Submitted prompt available: yes",
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
        "Answer the user query directly and transparently. No retrieved Mongo context matched this "
        "request, but the submitted prompt is still available. Treat the Runtime Facts as the "
        "source of truth about what happened in this request. If you discuss behind-the-scenes "
        "operation or context use, include this exact runtime fact: 'For this request, Mongo "
        "lookup ran but no matching Mongo context was used.' Then explain the visible Mnemosyne "
        "process in plain language: prompt intake, context lookup, tool or adapter calls, and "
        "answer generation. Do not withhold useful general answers solely because no matching "
        "context was retrieved."
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
    text = record.get("text") or record.get("text_preview") or ""
    lines = [
        f"### {record['role']} d={record['distance']}: {record.get('title') or '<untitled>'}",
        "",
        f"- Node ID: {record['node_id']}",
        f"- Labels: {labels}",
        f"- Endorsement: {record.get('endorsement_label') or '<none>'}",
        f"- Source: {provenance.get('archive_path') or provenance.get('source_path') or '<unknown>'}",
        "",
        text,
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
    serialized["text"] = node.get("text", "")
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
        "usage_score": parsed_usage_score(node.get("usage_score")),
        "usage_score_bonus": usage_score_bonus(node.get("usage_score")),
        "last_used_at": iso(node.get("last_used_at")),
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
