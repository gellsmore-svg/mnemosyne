from __future__ import annotations


def memory_health_payload(db) -> dict:
    source_chunks = collection_count(db, "nodes", {"labels": "source_chunk"})
    profiled_source_chunks = collection_count(
        db,
        "nodes",
        {"labels": "source_chunk", "embedding.vector": {"$exists": True}},
    )
    generated_outputs = collection_count(db, "nodes", {"labels": "generated_output"})
    pending_semantic_candidates = collection_count(
        db,
        "semantic_edge_candidates",
        {"status": "pending"},
    )
    pending_profile_jobs = collection_count(
        db,
        "embedding_backfill_jobs",
        {"status": "pending"},
    )
    pending_output_jobs = collection_count(db, "output_ingestion_queue", {"status": "pending"})
    failed_ingestion_jobs = collection_count(db, "queue", {"status": "failed"})
    failed_output_jobs = collection_count(db, "output_ingestion_queue", {"status": "failed"})
    profile_coverage = percentage(profiled_source_chunks, source_chunks)
    report = {
        "ok": True,
        "totals": {
            "documents": collection_count(db, "documents"),
            "trees": collection_count(db, "trees"),
            "nodes": collection_count(db, "nodes"),
            "source_chunks": source_chunks,
            "generated_outputs": generated_outputs,
            "graph_edges": collection_count(db, "graph_edges"),
            "semantic_edge_candidates": collection_count(db, "semantic_edge_candidates"),
        },
        "profile_coverage": {
            "profiled_source_chunks": profiled_source_chunks,
            "eligible_source_chunks": source_chunks,
            "percent": profile_coverage,
        },
        "endorsements": collection_value_counts(db, "nodes", "endorsement_label"),
        "queues": {
            "ingestion": collection_value_counts(db, "queue", "status"),
            "profile_backfill": collection_value_counts(db, "embedding_backfill_jobs", "status"),
            "output_ingestion": collection_value_counts(db, "output_ingestion_queue", "status"),
            "semantic_edge_candidates": collection_value_counts(
                db,
                "semantic_edge_candidates",
                "status",
            ),
        },
        "attention": [],
    }
    if source_chunks and profiled_source_chunks < source_chunks:
        report["attention"].append(
            {
                "kind": "profile_coverage",
                "message": (
                    "Some source chunks do not have profiles. Run "
                    "`tirzah queue-profile-backfill` and `tirzah process-profile-backfill`."
                ),
            }
        )
    if pending_profile_jobs:
        report["attention"].append(
            {
                "kind": "pending_profile_backfill",
                "message": f"{pending_profile_jobs} profile backfill job(s) are pending.",
            }
        )
    if pending_semantic_candidates:
        report["attention"].append(
            {
                "kind": "pending_semantic_review",
                "message": (
                    f"{pending_semantic_candidates} semantic edge candidate(s) await review."
                ),
            }
        )
    if pending_output_jobs:
        report["attention"].append(
            {
                "kind": "pending_output_ingestion",
                "message": f"{pending_output_jobs} generated output ingestion job(s) are pending.",
            }
        )
    if failed_ingestion_jobs or failed_output_jobs:
        report["attention"].append(
            {
                "kind": "failed_jobs",
                "message": (
                    f"{failed_ingestion_jobs} ingestion job(s) and "
                    f"{failed_output_jobs} output ingestion job(s) are failed."
                ),
            }
        )
    return report


def collection_count(db, collection_name: str, query: dict | None = None) -> int:
    collection = getattr(db, collection_name, None)
    if collection is None:
        return 0
    if hasattr(collection, "count_documents"):
        return int(collection.count_documents(query or {}))
    return len(collection_rows(collection, query or {}))


def collection_value_counts(db, collection_name: str, field: str) -> dict[str, int]:
    collection = getattr(db, collection_name, None)
    if collection is None:
        return {}
    if hasattr(collection, "aggregate"):
        try:
            rows = collection.aggregate(
                [
                    {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ]
            )
            return {
                str(row.get("_id") if row.get("_id") is not None else "missing"): int(
                    row["count"]
                )
                for row in rows
            }
        except Exception:
            pass
    counts: dict[str, int] = {}
    for row in collection_rows(collection, {}):
        value = nested_value(row, field)
        key = str(value if value is not None else "missing")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def collection_rows(collection, query: dict) -> list[dict]:
    if hasattr(collection, "find"):
        try:
            return list(collection.find(query))
        except TypeError:
            try:
                return list(collection.find(query, {}))
            except TypeError:
                pass
    return [row for row in getattr(collection, "rows", []) if row_matches_query(row, query)]


def row_matches_query(row: dict, query: dict) -> bool:
    for key, expected in query.items():
        value = nested_value(row, key)
        if isinstance(expected, dict):
            if expected.get("$exists") is True and value is None:
                return False
            if expected.get("$exists") is False and value is not None:
                return False
            continue
        if isinstance(value, list):
            if expected not in value:
                return False
        elif value != expected:
            return False
    return True


def nested_value(row: dict, dotted_key: str):
    value = row
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def percentage(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100, 1)


def render_memory_health_text(report: dict) -> str:
    lines = ["Memory health"]
    totals = report.get("totals", {})
    lines.append(
        "Corpus: "
        f"{totals.get('documents', 0)} document(s), "
        f"{totals.get('nodes', 0)} node(s), "
        f"{totals.get('source_chunks', 0)} source chunk(s)"
    )
    lines.append(
        "Graph: "
        f"{totals.get('graph_edges', 0)} edge(s), "
        f"{totals.get('semantic_edge_candidates', 0)} semantic candidate(s)"
    )
    coverage = report.get("profile_coverage", {})
    lines.append(
        "Profiles: "
        f"{coverage.get('profiled_source_chunks', 0)} / "
        f"{coverage.get('eligible_source_chunks', 0)} source chunk(s) "
        f"({coverage.get('percent', 0.0)}%)"
    )
    lines.append(f"Endorsements: {format_counts(report.get('endorsements', {}))}")
    queues = report.get("queues", {})
    lines.append(f"Ingestion queue: {format_counts(queues.get('ingestion', {}))}")
    lines.append(f"Profile jobs: {format_counts(queues.get('profile_backfill', {}))}")
    lines.append(f"Output ingestion: {format_counts(queues.get('output_ingestion', {}))}")
    lines.append(
        "Semantic review: "
        f"{format_counts(queues.get('semantic_edge_candidates', {}))}"
    )
    attention = report.get("attention", [])
    if attention:
        lines.append("Attention:")
        lines.extend(f"- {item['message']}" for item in attention)
    else:
        lines.append("Attention: none")
    return "\n".join(lines)


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
