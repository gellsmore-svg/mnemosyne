from __future__ import annotations

from pathlib import Path
from typing import Any

from mnemosyne.models.ingestion import IngestionResult


def ingestion_activity_report(
    *,
    path: Path | str,
    status: str,
    checksum_sha256: str | None = None,
    result: IngestionResult | None = None,
    inserted: dict[str, Any] | None = None,
    job_id: str | None = None,
    process_run_id: str | None = None,
    reason: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = result.source if result else None
    nodes = result.nodes if result else []
    relation_count = sum(len(node.relations) for node in nodes)
    labels = sorted({label for node in nodes for label in node.labels})
    inserted = inserted or {}
    details = details or {}
    stored_node_count = inserted.get("node_count")
    if stored_node_count is None and inserted.get("node_ids") is not None:
        stored_node_count = len(inserted["node_ids"])
    return {
        "kind": "ingestion_activity_report",
        "status": status,
        "source": {
            "path": str(path),
            "kind": source.kind if source else details.get("source_kind"),
            "checksum_sha256": checksum_sha256 or (source.checksum_sha256 if source else None),
            "archive_path": inserted.get("archive_path") or (source.archive_path if source else None),
            "processed_path": inserted.get("processed_path"),
        },
        "queue": {
            "job_id": job_id,
            "process_run_id": process_run_id,
        },
        "source_dates": {
            "origin_date": source.origin_date if source else None,
            "origin_date_source": source.origin_date_source if source else None,
            "candidate_count": len(source.date_candidates) if source else 0,
            "candidates": source.date_candidates if source else [],
        },
        "semantic_processing": {
            "adapter": result.adapter if result else None,
            "title": result.title if result else None,
            "summary": result.summary if result else None,
            "node_count": len(nodes),
            "relation_hint_count": relation_count,
            "labels": labels,
            "sample_nodes": [
                {
                    "title": node.title,
                    "labels": node.labels,
                    "relation_hint_count": len(node.relations),
                }
                for node in nodes[:5]
            ],
        },
        "repository_actions": {
            "document_id": inserted.get("document_id"),
            "tree_id": inserted.get("tree_id"),
            "node_count": stored_node_count,
            "edge_count": inserted.get("edge_count"),
            "ingestion_epoch": inserted.get("ingestion_epoch") or (result.ingestion_epoch if result else None),
        },
        "outcome": {
            "reason": reason,
            "message": message,
            "details": details,
        },
    }


def ingestion_activity_log(report: dict[str, Any]) -> str:
    source = report.get("source") or {}
    dates = report.get("source_dates") or {}
    semantic = report.get("semantic_processing") or {}
    repo = report.get("repository_actions") or {}
    outcome = report.get("outcome") or {}
    queue = report.get("queue") or {}

    lines = [
        "Ingestion Activity Log",
        f"- Status: {report.get('status') or 'unknown'}.",
        f"- Source: {source.get('path') or 'unknown'}"
        f" ({source.get('kind') or 'type unknown'}).",
    ]
    if source.get("checksum_sha256"):
        lines.append(f"- Checksum: {source['checksum_sha256']}.")
    if queue.get("job_id"):
        lines.append(f"- Queue job: {queue['job_id']}.")
    if queue.get("process_run_id"):
        lines.append(f"- Process run: {queue['process_run_id']}.")

    if outcome.get("reason"):
        lines.append(f"- Outcome reason: {outcome['reason']}.")
    if outcome.get("message"):
        lines.append(f"- Operator note: {outcome['message']}")

    if dates.get("origin_date"):
        lines.append(
            "- Source dating: selected "
            f"{dates['origin_date']} from {human_date_source(dates.get('origin_date_source'))}; "
            f"{dates.get('candidate_count', 0)} candidate(s) inspected."
        )
    elif dates.get("candidate_count"):
        lines.append(f"- Source dating: {dates['candidate_count']} candidate(s) inspected; no date selected.")

    if semantic.get("adapter"):
        lines.append(f"- Semantic processing: {semantic['adapter']} generated {semantic.get('node_count', 0)} node(s).")
    if semantic.get("relation_hint_count"):
        lines.append(f"- Relationship hints: {semantic['relation_hint_count']} proposed by ingestion.")
    if semantic.get("labels"):
        lines.append(f"- Labels applied: {', '.join(semantic['labels'])}.")

    if repo.get("document_id"):
        lines.append(
            "- Repository write: document "
            f"{repo['document_id']} with {repo.get('node_count', 0)} node(s)"
            f" in epoch {repo.get('ingestion_epoch') or 'unknown'}."
        )
    if source.get("archive_path"):
        lines.append(f"- Archive: {source['archive_path']}.")
    if source.get("processed_path"):
        lines.append(f"- Processed source moved to: {source['processed_path']}.")

    details = outcome.get("details") or {}
    if details.get("dead_letter_path"):
        lines.append(f"- Dead letter: {details['dead_letter_path']}.")
    if details.get("existing_document_id"):
        lines.append(f"- Existing document: {details['existing_document_id']}.")
    if details.get("error"):
        lines.append(f"- Error: {details['error']}.")

    return "\n".join(lines)


def attach_ingestion_activity(result: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    result["activity_report"] = report
    result["activity_log"] = ingestion_activity_log(report)
    return result


def human_date_source(source: str | None) -> str:
    labels = {
        "explicit_content": "an explicit date in the document",
        "filename": "the filename",
        "file_created": "the filesystem creation/change time",
        "file_modified": "the filesystem modification time",
    }
    return labels.get(source or "", source or "unknown evidence")
