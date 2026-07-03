"""Ingestion/backfill status analytics (moved out of the web layer).

The narrated operator status behind ``/api/ingestion/status`` and the inbox
endpoints: ingestion epochs, text-similarity-profile (embedding) coverage with
its recommended next action, backfill-job status and job sizing, the staged
ingest-folder listing, and the human activity log for an inbox run. Pure
db-and-dict logic — no HTTP concerns.
"""

from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Any

from tirzah.ingestion.dates import analyze_source_dates
from tirzah.ingestion.embedding_backfill import list_embedding_backfill_jobs
from tirzah.ingestion.parser import read_text_source
from tirzah.ingestion.worker import discover_sources

WEB_EMBEDDING_BACKFILL_MAX_BATCHES = 10
WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT = 25


def process_inbox_activity_log(enqueued: list[dict[str, Any]], processed: list[dict[str, Any]]) -> str:
    lines = [
        "Inbox Processing Activity Log",
        f"- Staged source files inspected: {len(enqueued)}.",
        f"- Ingestion jobs processed: {len(processed)}.",
    ]
    if enqueued:
        rejected = [job for job in enqueued if job.get("status") == "rejected"]
        lines.append(f"- Queue intake: {len(enqueued) - len(rejected)} accepted, {len(rejected)} rejected.")
    if not processed:
        lines.append("- Result: no pending ingestion jobs were available to process.")
        return "\n".join(lines)
    for index, result in enumerate(processed, start=1):
        log = result.get("activity_log")
        if log:
            lines.append("")
            lines.append(f"Run {index}")
            lines.append(log)
        else:
            lines.append("")
            lines.append(
                f"Run {index}: {result.get('status', 'unknown')} for "
                f"{result.get('path') or result.get('document_id') or 'unknown source'}."
            )
    return "\n".join(lines)


def ingest_folder_file_rows(ingest_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in discover_sources(ingest_dir):
        try:
            stat = path.stat()
            text, _source_kind = read_text_source(path)
            date_analysis = analyze_source_dates(path, text)
        except Exception as error:
            rows.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "suffix": path.suffix.lower(),
                    "bytes": None,
                    "modified_at": None,
                    "origin_date": None,
                    "origin_date_source": None,
                    "date_candidate_count": 0,
                    "status": "unreadable",
                    "error": error.__class__.__name__,
                    "message": str(error),
                }
            )
            continue
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "suffix": path.suffix.lower(),
                "bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "origin_date": date_analysis.get("origin_date"),
                "origin_date_source": date_analysis.get("origin_date_source"),
                "date_candidate_count": len(date_analysis.get("date_candidates") or []),
                "status": "ready",
            }
        )
    return sorted(rows, key=ingest_folder_sort_key)


def ingest_folder_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row.get("origin_date") or "9999-12-31", row.get("path") or "")


def list_ingestion_epochs(db: Any, limit: int = 8) -> list[dict[str, Any]]:
    rows = db.documents.aggregate(
        [
            {
                "$group": {
                    "_id": "$ingestion_epoch",
                    "document_count": {"$sum": 1},
                    "dated_document_count": {
                        "$sum": {
                            "$cond": [
                                {"$ne": [{"$ifNull": ["$source.origin_date", None]}, None]},
                                1,
                                0,
                            ]
                        }
                    },
                    "first_created_at": {"$min": "$created_at"},
                    "last_updated_at": {"$max": "$updated_at"},
                    "earliest_origin_date": {
                        "$min": {
                            "$cond": [
                                {"$ne": [{"$ifNull": ["$source.origin_date", None]}, None]},
                                "$source.origin_date",
                                "9999-12-31",
                            ]
                        }
                    },
                    "latest_origin_date": {"$max": "$source.origin_date"},
                }
            },
            {"$sort": {"last_updated_at": -1}},
            {"$limit": max(1, min(int(limit), 50))},
        ]
    )
    epochs = []
    for row in rows:
        earliest_origin_date = row.get("earliest_origin_date")
        if earliest_origin_date == "9999-12-31":
            earliest_origin_date = None
        epochs.append(
            {
                "ingestion_epoch": row.get("_id") or "unknown",
                "document_count": row.get("document_count", 0),
                "dated_document_count": row.get("dated_document_count", 0),
                "first_created_at": serialize_web_value(row.get("first_created_at")),
                "last_updated_at": serialize_web_value(row.get("last_updated_at")),
                "earliest_origin_date": earliest_origin_date,
                "latest_origin_date": row.get("latest_origin_date"),
            }
        )
    return epochs


def embedding_coverage(db: Any, label: str | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {"status": {"$ne": "superseded"}}
    label_filter = str(label).strip() if label else None
    if label_filter:
        query["labels"] = label_filter
    embedded_query = {
        **query,
        "embedding.vector": {"$exists": True},
    }
    total = db.nodes.count_documents(query)
    embedded = db.nodes.count_documents(embedded_query)
    missing = max(0, total - embedded)
    percent = round((embedded / total) * 100, 1) if total else 0.0
    return annotate_embedding_coverage({
        "total_active_nodes": total,
        "embedded_active_nodes": embedded,
        "missing_active_embeddings": missing,
        "embedded_percent": percent,
        "profiles": embedding_profile_counts(db, embedded_query),
        "label": label_filter,
    })


def embedding_profile_counts(db: Any, embedded_query: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.nodes.aggregate(
        [
            {"$match": embedded_query},
            {
                "$group": {
                    "_id": {
                        "adapter": "$embedding.adapter",
                        "model": "$embedding.model",
                        "dimensions": "$embedding.dimensions",
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
    )
    return [
        {
            "adapter": (row.get("_id") or {}).get("adapter"),
            "model": (row.get("_id") or {}).get("model"),
            "dimensions": (row.get("_id") or {}).get("dimensions"),
            "count": row.get("count", 0),
            "is_mock": str((row.get("_id") or {}).get("adapter") or "").startswith("mock"),
        }
        for row in rows
    ]


def annotate_embedding_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    total = int(coverage.get("total_active_nodes") or 0)
    embedded = int(coverage.get("embedded_active_nodes") or 0)
    missing = int(coverage.get("missing_active_embeddings") or 0)
    profiles = coverage.get("profiles") or []
    mock_count = sum(int(profile.get("count") or 0) for profile in profiles if profile.get("is_mock"))
    profile_count = len(profiles)
    label = coverage.get("label")
    scope = f" for label `{label}`" if label else ""

    if total == 0:
        status = "empty"
        summary = f"No active nodes are available{scope}."
        action = "Ingest source documents before building text similarity profiles or reviewing profile-based semantic candidates."
    elif embedded == 0:
        status = "not_started"
        summary = f"{total} active node(s){scope} exist, but none have text similarity profiles yet."
        action = "Queue a scoped profile backfill job, then process bounded batches until coverage begins to rise."
    elif missing > 0:
        status = "incomplete"
        summary = f"{embedded} of {total} active node(s){scope} have text similarity profiles; {missing} still need profiles."
        action = "Continue processing profile backfill job batches before relying on profile-based candidate review."
    elif mock_count:
        status = "mock_only" if mock_count == embedded else "mixed_mock"
        summary = f"All active nodes{scope} have text similarity profiles, but {mock_count} node(s) use stub profiles."
        action = "Re-run a forced profile backfill with a local model-backed profile adapter before treating profile-based relationships as semantic evidence."
    else:
        status = "ready"
        summary = f"All {total} active node(s){scope} have model-backed text similarity profiles."
        action = "Preview profile matches, enqueue promising semantic-edge candidates, and review them before answer use."

    warnings = []
    if profile_count > 1:
        warnings.append(f"{profile_count} profile representations are present; compare models and dimensions before broad profile-based review.")
    if mock_count and missing:
        warnings.append("Some existing text similarity profiles are stubs while other active nodes still have no profiles.")

    return {
        **coverage,
        "status": status,
        "summary": summary,
        "recommended_action": action,
        "warnings": warnings,
    }


def embedding_backfill_status(
    db: Any,
    coverage: dict[str, Any],
    limit: int = 20,
    embedding_adapter_allowed: bool = True,
    configured_embedding_adapter: str | None = None,
    profile_adapter_status: dict[str, Any] | None = None,
    recommended_batch_limit: int = WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT,
    web_max_batches: int = WEB_EMBEDDING_BACKFILL_MAX_BATCHES,
) -> dict[str, Any]:
    jobs = list_embedding_backfill_jobs(db, limit=limit)
    counts: dict[str, int] = {}
    for job in jobs:
        status = job.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    next_job = next(
        (job for job in jobs if job.get("status") in {"pending", "processing", "blocked"}),
        None,
    )
    missing = int(coverage.get("missing_active_embeddings") or 0)
    adapter_blocked = not embedding_adapter_allowed and (
        missing > 0 or coverage.get("status") in {"mock_only", "mixed_mock"}
    )
    adapter_status = profile_adapter_status or {}
    local_command_missing = adapter_status.get("status") == "missing_profile_command" and (
        missing > 0 or coverage.get("status") in {"mock_only", "mixed_mock"}
    )
    if adapter_blocked:
        status = "embedding_adapter_blocked"
        summary = (
            f"Configured profile adapter `{configured_embedding_adapter or 'unknown'}` is not allowed "
            "for ingestion or retrieval memory operations."
        )
        action = "Configure a local non-HTTP profile adapter before queueing or processing profile backfill."
    elif local_command_missing:
        status = "profile_command_missing"
        summary = "The local command profile adapter is selected, but no profile command is configured."
        action = "Configure runtime.profile_command before queueing or processing profile backfill."
    elif counts.get("pending"):
        status = "pending"
        summary = f"{counts['pending']} recent profile backfill job(s) are queued."
        action = "Process the next bounded backfill batches and refresh ingestion status."
    elif counts.get("processing"):
        status = "processing"
        summary = f"{counts['processing']} recent profile backfill job(s) are marked processing."
        action = "Refresh status after the current worker finishes; if this persists, inspect the job for a stuck processing state."
    elif counts.get("blocked"):
        status = "blocked"
        summary = f"{counts['blocked']} recent profile backfill job(s) are blocked."
        action = "Open the latest job log, resolve the recorded error, then queue a new scoped backfill if needed."
    elif coverage.get("status") in {"mock_only", "mixed_mock"}:
        status = "real_backfill_needed"
        summary = "Active nodes have text similarity profiles, but at least some are stubs."
        action = "Configure a local model-backed profile adapter, then queue a forced profile backfill before profile-based review."
    elif missing > 0:
        status = "needed"
        summary = "No recent active backfill job is queued, but active nodes still lack text similarity profiles."
        action = "Queue a profile backfill job with a conservative batch size."
    else:
        status = "not_needed"
        summary = "No profile backfill job is currently needed for active-node coverage."
        action = "Move to profile-match preview and reviewed semantic-edge candidate work."

    return {
        "status": status,
        "summary": summary,
        "recommended_action": action,
        "recommended_job": None
        if adapter_blocked or local_command_missing
        else recommended_embedding_backfill_job(
            coverage,
            recommended_batch_limit=recommended_batch_limit,
            web_max_batches=web_max_batches,
        ),
        "recent_status_counts": counts,
        "recent_jobs_checked": len(jobs),
        "next_job": next_job,
    }


def recommended_embedding_backfill_job(
    coverage: dict[str, Any],
    *,
    recommended_batch_limit: int = WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT,
    web_max_batches: int = WEB_EMBEDDING_BACKFILL_MAX_BATCHES,
) -> dict[str, Any] | None:
    configured_batch_limit = max(1, min(int(recommended_batch_limit or 1), 1000))
    configured_web_batches = max(1, min(int(web_max_batches or 1), 100))
    missing = int(coverage.get("missing_active_embeddings") or 0)
    coverage_status = coverage.get("status")
    if coverage_status in {"mock_only", "mixed_mock"}:
        target_count = int(coverage.get("total_active_nodes") or coverage.get("embedded_active_nodes") or 0)
        if target_count <= 0:
            return None
        batch_limit = min(configured_batch_limit, target_count)
        total_batches = ceil(target_count / batch_limit) if batch_limit else 0
        recommended_web_batches = min(configured_web_batches, total_batches)
        return {
            "batch_limit": batch_limit,
            "force": True,
            "missing_embedding_only": False,
            "requires_real_adapter": True,
            "estimated_total_batches": total_batches,
            "recommended_web_batches": recommended_web_batches,
            "estimated_nodes_per_web_run": batch_limit * recommended_web_batches,
            "summary": (
                f"After configuring a local model-backed profile adapter, queue a forced backfill with batch limit {batch_limit}; "
                f"process up to {recommended_web_batches} batch(es) per web run. "
                f"Current coverage needs about {total_batches} total forced batch(es)."
            ),
        }
    if missing <= 0:
        return None
    batch_limit = min(configured_batch_limit, missing)
    total_batches = ceil(missing / batch_limit) if batch_limit else 0
    recommended_web_batches = min(configured_web_batches, total_batches)
    return {
        "batch_limit": batch_limit,
        "force": False,
        "missing_embedding_only": True,
        "requires_real_adapter": False,
        "estimated_total_batches": total_batches,
        "recommended_web_batches": recommended_web_batches,
        "estimated_nodes_per_web_run": batch_limit * recommended_web_batches,
        "summary": (
            f"Queue a missing-profile job with batch limit {batch_limit}; "
            f"process up to {recommended_web_batches} batch(es) per web run. "
            f"Current coverage needs about {total_batches} total batch(es)."
        ),
    }


def embedding_backfill_batch_failure_reason(result: dict[str, Any]) -> str:
    for batch in reversed(result.get("results") or []):
        batch_result = batch.get("result") or {}
        if batch_result.get("reason"):
            return batch_result["reason"]
    return "embedding_backfill_job_failed"


def embedding_backfill_batch_step_ids(result: dict[str, Any]) -> list[str]:
    steps = []
    for index, batch in enumerate(result.get("results") or [], start=1):
        status = batch.get("status") or "unknown"
        steps.append(f"embedding_backfill_job_batch_{index}_{status}")
    return steps


def serialize_web_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
