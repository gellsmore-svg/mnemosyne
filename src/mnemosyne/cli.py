from __future__ import annotations

import argparse
import json
from pathlib import Path

from bson import ObjectId

from mnemosyne.adapters.embedding import embedding_adapter
from mnemosyne.adapters.mock import MockIngestionAdapter
from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.db.governance import (
    PROCESS_RUN_STATUSES,
    create_process_run,
    get_agent_identity,
    get_governance_policy,
    get_process_object,
    get_process_run,
    get_trust_weighting_profile,
    list_agent_identities,
    list_governance_policies,
    list_process_objects,
    list_process_runs,
    list_trust_weighting_profiles,
    update_process_run,
)
from mnemosyne.db.indexes import ensure_indexes
from mnemosyne.db.repositories import (
    DuplicateSourceError,
    backfill_schema_metadata,
    backfill_structural_graph_edges,
    commit_ingestion,
    create_reviewed_semantic_edge,
    enqueue_semantic_edge_candidates,
    find_duplicate_by_checksum,
    document_tree,
    graph_edge_status,
    label_definitions,
    list_semantic_edge_candidates,
    rebuild_document,
    review_semantic_edge_candidate,
)
from mnemosyne.db.queue import enqueue_source, queue_summary, recent_jobs
from mnemosyne.ingestion.activity import attach_ingestion_activity, ingestion_activity_report
from mnemosyne.ingestion.dates import analyze_source_dates, annotate_source_dates
from mnemosyne.ingestion.files import archive_source, move_request_file, sha256_file
from mnemosyne.ingestion.parser import SUPPORTED_SUFFIXES, read_text_source
from mnemosyne.ingestion.worker import discover_sources, process_next
from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    compile_context,
    expand_graph_paths,
    expand_proximity,
    get_document,
    graph_edges_for_node,
    list_documents,
    node_context,
    parse_iso_datetime,
    render_context_document,
    search_nodes,
    semantic_candidate_nodes,
)
from mnemosyne.retrieval.trust import trust_temporal_diagnostic_for_node
from mnemosyne.sessions.active_documents import list_active_documents
from mnemosyne.sessions.exchanges import recent_exchanges
from mnemosyne.sessions.endorsements import (
    ENDORSEMENT_LABELS,
    list_generated_output_nodes,
    update_node_endorsement,
)
from mnemosyne.sessions.interaction import answer_query
from mnemosyne.sessions.output_ingestion import (
    list_output_ingestion_jobs,
    process_next_output_ingestion,
)
from mnemosyne.sessions.registry import create_session, list_sessions


STRUCTURAL_NODE_LABELS = {"source_root", "source_section", "source_chunk"}


def discover_folder_sources(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and ".git" not in path.parts
    )


def chronological_folder_source_plan(root: Path) -> list[dict]:
    plan = []
    for path in discover_folder_sources(root):
        try:
            text, _source_kind = read_text_source(path)
            date_analysis = analyze_source_dates(path, text)
        except Exception as error:
            plan.append(
                {
                    "path": path,
                    "origin_date": None,
                    "origin_date_source": None,
                    "date_candidates": [],
                    "error": error.__class__.__name__,
                    "message": str(error),
                }
            )
            continue
        plan.append(
            {
                "path": path,
                "origin_date": date_analysis.get("origin_date"),
                "origin_date_source": date_analysis.get("origin_date_source"),
                "date_candidates": date_analysis.get("date_candidates") or [],
            }
        )
    return sorted(plan, key=chronological_source_sort_key)


def chronological_source_sort_key(item: dict) -> tuple[str, str]:
    origin_date = item.get("origin_date") or "9999-12-31"
    return (origin_date, str(item.get("path") or ""))


def ingest_source_path(
    db,
    config,
    path: Path,
    labels: list[str],
    ingestion_epoch: str | None = None,
) -> dict:
    checksum = sha256_file(path)
    duplicate = find_duplicate_by_checksum(db, checksum)
    if duplicate:
        rejected = {
            "ok": False,
            "path": str(path),
            "status": "rejected",
            "reason": "duplicate_checksum",
            "checksum_sha256": checksum,
            "existing_document_id": str(duplicate["_id"]),
            "message": "File rejected because identical content has already been ingested.",
        }
        report = ingestion_activity_report(
            path=path,
            status="rejected",
            checksum_sha256=checksum,
            reason="duplicate_checksum",
            message=rejected["message"],
            details={"existing_document_id": rejected["existing_document_id"]},
        )
        return attach_ingestion_activity(rejected, report)

    text, source_kind = read_text_source(path)
    result = MockIngestionAdapter().process(
        path,
        text,
        source_kind,
        extra_labels=labels,
    )
    annotate_source_dates(result, path, text)
    result.ingestion_epoch = ingestion_epoch
    archived_path = archive_source(path, config.paths.archive, checksum)
    result.source.checksum_sha256 = checksum
    result.source.archive_path = str(archived_path)
    try:
        inserted = commit_ingestion(db, result, embedder=embedding_adapter(config.runtime))
    except DuplicateSourceError as error:
        rejected = {
            "ok": False,
            "path": str(path),
            "status": "rejected",
            "reason": "duplicate_checksum",
            "checksum_sha256": error.checksum,
            "existing_document_id": str(error.existing_document_id),
            "message": "File rejected because identical content has already been ingested.",
        }
        report = ingestion_activity_report(
            path=path,
            status="rejected",
            checksum_sha256=error.checksum,
            result=result,
            reason="duplicate_checksum",
            message=rejected["message"],
            details={"existing_document_id": rejected["existing_document_id"]},
        )
        return attach_ingestion_activity(rejected, report)
    inserted["ok"] = True
    inserted["path"] = str(path)
    inserted["archive_path"] = str(archived_path)
    inserted["checksum_sha256"] = checksum
    report = ingestion_activity_report(
        path=path,
        status="completed",
        checksum_sha256=checksum,
        result=result,
        inserted=inserted,
    )
    return attach_ingestion_activity(inserted, report)


def rejection_reason_counts(results: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        reason = result.get("reason", "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def existing_document_extra_labels(db, document_id: str) -> list[str]:
    labels = set()
    for row in db.nodes.find({"document_id": ObjectId(document_id)}, {"labels": 1}):
        labels.update(row.get("labels", []))
    return sorted(label for label in labels if label not in STRUCTURAL_NODE_LABELS)


def document_ids_for_label(db, label: str) -> list[str]:
    document_ids = db.nodes.distinct("document_id", {"labels": label})
    return sorted(str(document_id) for document_id in document_ids)


def destructive_rebuild_refusal(command: str) -> dict:
    return {
        "ok": False,
        "reason": "destructive_rebuild_requires_force_replace",
        "command": command,
        "message": (
            "This command deletes and recreates existing trees/nodes. Requirements call for "
            "versioned replacement, not silent deletion. Re-run with --force-replace only for "
            "explicit maintenance work."
        ),
    }


def rebuild_document_from_existing_source(
    db,
    document_id: str,
    source_override: str | None = None,
    ingestion_epoch: str | None = None,
) -> dict:
    document = get_document(db, document_id)
    if not document:
        return {
            "ok": False,
            "reason": "document_not_found",
            "document_id": document_id,
        }
    source = document.get("source", {})
    source_path = Path(
        source_override
        or source.get("archive_path")
        or source.get("path")
        or ""
    )
    if not source_path.exists():
        return {
            "ok": False,
            "reason": "source_missing",
            "document_id": document_id,
            "path": str(source_path),
        }
    text, source_kind = read_text_source(source_path)
    adapter_path = Path(source.get("path") or source_path)
    result = MockIngestionAdapter().process(
        adapter_path,
        text,
        source_kind,
        extra_labels=existing_document_extra_labels(db, document_id),
    )
    annotate_source_dates(result, adapter_path, text)
    result.source.path = source.get("path") or str(source_path)
    result.source.checksum_sha256 = source.get("checksum_sha256") or sha256_file(source_path)
    result.source.archive_path = source.get("archive_path") or str(source_path)
    result.ingestion_epoch = ingestion_epoch
    inserted = rebuild_document(db, document_id, result)
    inserted["ok"] = True
    inserted["source_path"] = str(source_path)
    inserted["checksum_sha256"] = result.source.checksum_sha256
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(prog="mnemosyne")
    parser.add_argument("--config", default="config.yaml")

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("db-ping")
    subcommands.add_parser("backfill-source-metadata")
    subcommands.add_parser("backfill-schema-metadata")
    graph_status = subcommands.add_parser("graph-status")
    graph_status.add_argument("--limit", type=int, default=10)
    structural_edges = subcommands.add_parser("backfill-structural-graph-edges")
    structural_edges.add_argument("--limit", type=int, default=None)
    subcommands.add_parser("enqueue-inbox")
    subcommands.add_parser("process-next")
    subcommands.add_parser("process-inbox")
    subcommands.add_parser("queue-status")
    subcommands.add_parser("labels")
    subcommands.add_parser("sessions")

    agent_identities = subcommands.add_parser("agent-identities")
    agent_identities.add_argument("--limit", type=int, default=20)
    agent_identity = subcommands.add_parser("agent-identity")
    agent_identity.add_argument("identity_id")

    trust_profiles = subcommands.add_parser("trust-weighting-profiles")
    trust_profiles.add_argument("--limit", type=int, default=20)
    trust_profile = subcommands.add_parser("trust-weighting-profile")
    trust_profile.add_argument("weighting_profile_id")
    trust_diagnostic = subcommands.add_parser("trust-diagnostic")
    trust_diagnostic.add_argument("node_id")
    trust_diagnostic.add_argument("--profile-id", default=None)

    governance_policies = subcommands.add_parser("governance-policies")
    governance_policies.add_argument("--limit", type=int, default=20)
    governance_policy = subcommands.add_parser("governance-policy")
    governance_policy.add_argument("policy_id")

    process_objects = subcommands.add_parser("process-objects")
    process_objects.add_argument("--limit", type=int, default=20)
    process_object = subcommands.add_parser("process-object")
    process_object.add_argument("process_id")

    process_runs = subcommands.add_parser("process-runs")
    process_runs.add_argument("--session-id", default=None)
    process_runs.add_argument("--status", default=None, choices=sorted(PROCESS_RUN_STATUSES))
    process_runs.add_argument("--limit", type=int, default=20)
    process_run = subcommands.add_parser("process-run")
    process_run.add_argument("run_id")
    start_process_run = subcommands.add_parser("start-process-run")
    start_process_run.add_argument("process_id")
    start_process_run.add_argument("--session-id", default="default")
    start_process_run.add_argument("--identity-id", default=None)
    start_process_run.add_argument("--current-step-id", default=None)
    start_process_run.add_argument("--status", default="active", choices=sorted(PROCESS_RUN_STATUSES))
    update_process_run_parser = subcommands.add_parser("update-process-run")
    update_process_run_parser.add_argument("run_id")
    update_process_run_parser.add_argument("--status", default=None, choices=sorted(PROCESS_RUN_STATUSES))
    update_process_run_parser.add_argument("--current-step-id", default=None)
    update_process_run_parser.add_argument("--completed-step-id", default=None)
    update_process_run_parser.add_argument("--exchange-id", default=None)
    update_process_run_parser.add_argument("--exception-reason", default=None)
    update_process_run_parser.add_argument("--exception-proposal", default=None)
    update_process_run_parser.add_argument("--exception-reviewer", default=None)
    update_process_run_parser.add_argument("--exception-note", default=None)

    active_documents = subcommands.add_parser("active-documents")
    active_documents.add_argument("--session-id", default="default")
    active_documents.add_argument("--limit", type=int, default=20)

    output_jobs = subcommands.add_parser("output-ingestion")
    output_jobs.add_argument("--session-id", default=None)
    output_jobs.add_argument("--status", default=None)
    output_jobs.add_argument("--limit", type=int, default=20)
    subcommands.add_parser("process-output-ingestion")

    review_outputs = subcommands.add_parser("review-generated-output")
    review_outputs.add_argument("--endorsement", default=None, choices=sorted(ENDORSEMENT_LABELS))
    review_outputs.add_argument("--limit", type=int, default=20)

    endorse_node = subcommands.add_parser("endorse-node")
    endorse_node.add_argument("node_id")
    endorse_node.add_argument("--endorsement", required=True, choices=sorted(ENDORSEMENT_LABELS))
    endorse_node.add_argument("--reviewer", default="user")
    endorse_node.add_argument("--note", default=None)

    create_session_cmd = subcommands.add_parser("create-session")
    create_session_cmd.add_argument("--title", default=None)
    create_session_cmd.add_argument("--session-id", default=None)

    list_docs = subcommands.add_parser("list-docs")
    list_docs.add_argument("--limit", type=int, default=20)

    show_doc = subcommands.add_parser("show-doc")
    show_doc.add_argument("document_id")

    search = subcommands.add_parser("search-nodes")
    search.add_argument("--query", default=None)
    search.add_argument("--label", default=None)
    search.add_argument("--endorsement", default=None)
    search.add_argument("--document-id", default=None)
    search.add_argument("--created-after", default=None)
    search.add_argument("--created-before", default=None)
    search.add_argument("--limit", type=int, default=20)

    context = subcommands.add_parser("node-context")
    context.add_argument("node_id")
    context.add_argument("--child-limit", type=int, default=20)

    graph_edges = subcommands.add_parser("graph-edges")
    graph_edges.add_argument("node_id")
    graph_edges.add_argument("--direction", choices=["incoming", "outgoing", "both"], default="both")
    graph_edges.add_argument("--relation-type", default=None)
    graph_edges.add_argument("--limit", type=int, default=10)

    proximity = subcommands.add_parser("expand-proximity")
    proximity.add_argument("node_id")
    proximity.add_argument("--direction", choices=["incoming", "outgoing", "both"], default="both")
    proximity.add_argument("--relation-type", default=None)
    proximity.add_argument("--limit", type=int, default=10)

    graph_paths = subcommands.add_parser("expand-graph-paths")
    graph_paths.add_argument("node_id")
    graph_paths.add_argument("--direction", choices=["incoming", "outgoing", "both"], default="both")
    graph_paths.add_argument("--relation-type", default=None)
    graph_paths.add_argument("--max-depth", type=int, default=2)
    graph_paths.add_argument("--branch-limit", type=int, default=5)
    graph_paths.add_argument("--limit", type=int, default=10)

    semantic_candidates = subcommands.add_parser("semantic-candidates")
    semantic_candidates.add_argument("node_id")
    semantic_candidates.add_argument("--include-same-document", action="store_true")
    semantic_candidates.add_argument("--limit", type=int, default=10)

    enqueue_semantic = subcommands.add_parser("enqueue-semantic-candidates")
    enqueue_semantic.add_argument("node_id")
    enqueue_semantic.add_argument("--include-same-document", action="store_true")
    enqueue_semantic.add_argument("--relation-type", default="related_to")
    enqueue_semantic.add_argument("--created-by", default="user")
    enqueue_semantic.add_argument("--limit", type=int, default=10)

    semantic_queue = subcommands.add_parser("semantic-edge-candidates")
    semantic_queue.add_argument("--status", default="pending")
    semantic_queue.add_argument("--limit", type=int, default=20)

    semantic_candidate_review = subcommands.add_parser("review-semantic-edge-candidate")
    semantic_candidate_review.add_argument("candidate_id")
    semantic_candidate_review.add_argument("--action", required=True, choices=["accept", "reject"])
    semantic_candidate_review.add_argument("--reviewer", default="user")
    semantic_candidate_review.add_argument("--note", default=None)
    semantic_candidate_review.add_argument("--weight", type=float, default=0.7)
    semantic_candidate_review.add_argument("--confidence", type=float, default=0.6)

    semantic_edge = subcommands.add_parser("create-semantic-edge")
    semantic_edge.add_argument("source_node_id")
    semantic_edge.add_argument("target_node_id")
    semantic_edge.add_argument("--relation-type", default="related_to")
    semantic_edge.add_argument("--weight", type=float, default=0.7)
    semantic_edge.add_argument("--confidence", type=float, default=0.6)
    semantic_edge.add_argument("--reviewer", default="user")
    semantic_edge.add_argument("--note", default=None)

    compile_ctx = subcommands.add_parser("compile-context")
    compile_ctx.add_argument("node_id")
    compile_ctx.add_argument("--ancestor-depth", type=int, default=3)
    compile_ctx.add_argument("--sibling-window", type=int, default=1)
    compile_ctx.add_argument("--child-depth", type=int, default=1)
    compile_ctx.add_argument("--child-limit", type=int, default=20)

    render_ctx = subcommands.add_parser("render-context")
    render_ctx.add_argument("node_id")
    render_ctx.add_argument("--ancestor-depth", type=int, default=3)
    render_ctx.add_argument("--sibling-window", type=int, default=1)
    render_ctx.add_argument("--child-depth", type=int, default=1)
    render_ctx.add_argument("--child-limit", type=int, default=20)
    render_ctx.add_argument("--char-budget", type=int, default=None)
    render_ctx.add_argument("--json", action="store_true")

    prompt = subcommands.add_parser("build-prompt")
    prompt.add_argument("node_id")
    prompt.add_argument("--query", required=True)
    prompt.add_argument("--ancestor-depth", type=int, default=3)
    prompt.add_argument("--sibling-window", type=int, default=1)
    prompt.add_argument("--child-depth", type=int, default=1)
    prompt.add_argument("--child-limit", type=int, default=20)
    prompt.add_argument("--token-budget", type=int, default=None)
    prompt.add_argument("--reserved-response-tokens", type=int, default=None)
    prompt.add_argument("--system-instruction", default=None)
    prompt.add_argument("--text", action="store_true")

    ask = subcommands.add_parser("ask")
    ask.add_argument("query")
    ask.add_argument("--node-id", default=None)
    ask.add_argument("--session-id", default="default")
    ask.add_argument("--adapter", default=None)
    ask.add_argument("--model", default=None)
    ask.add_argument("--retrieval-mode", choices=["direct", "agentic"], default=None)
    ask.add_argument("--json", action="store_true")

    chat = subcommands.add_parser("chat")
    chat.add_argument("--node-id", default=None)
    chat.add_argument("--session-id", default="default")
    chat.add_argument("--adapter", default=None)
    chat.add_argument("--model", default=None)
    chat.add_argument("--retrieval-mode", choices=["direct", "agentic"], default=None)

    history = subcommands.add_parser("history")
    history.add_argument("--session-id", default=None)
    history.add_argument("--query", default=None)
    history.add_argument("--adapter", default=None)
    history.add_argument("--model", default=None)
    history.add_argument("--limit", type=int, default=10)

    queue_recent = subcommands.add_parser("queue-recent")
    queue_recent.add_argument("--limit", type=int, default=10)
    queue_recent.add_argument("--status", default=None)
    queue_recent.add_argument("--query", default=None)
    queue_recent.add_argument("--reason", default=None)

    show_tree = subcommands.add_parser("show-tree")
    show_tree.add_argument("document_id")

    ingest_one = subcommands.add_parser("ingest-one")
    ingest_one.add_argument("path")
    ingest_one.add_argument(
        "--label",
        action="append",
        default=[],
        help="Additional node label to apply to every ingested node. May be repeated.",
    )
    ingest_one.add_argument(
        "--ingestion-epoch",
        default=None,
        help="Optional epoch identifier to stamp on the document, tree, and nodes.",
    )

    ingest_folder = subcommands.add_parser("ingest-folder")
    ingest_folder.add_argument("path")
    ingest_folder.add_argument(
        "--label",
        action="append",
        default=[],
        help="Additional node label to apply to every ingested node. May be repeated.",
    )
    ingest_folder.add_argument(
        "--ingestion-epoch",
        default=None,
        help="Optional epoch identifier to stamp on every ingested document, tree, and node.",
    )
    ingest_folder.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of files to ingest from the folder.",
    )
    ingest_folder.add_argument(
        "--include-results",
        action="store_true",
        help="Include every per-file result in the JSON output. By default only a summary is printed.",
    )

    rebuild_doc = subcommands.add_parser("rebuild-document")
    rebuild_doc.add_argument("document_id")
    rebuild_doc.add_argument(
        "--source",
        default=None,
        help="Optional source path override. Defaults to the document archive path, then original source path.",
    )
    rebuild_doc.add_argument(
        "--force-replace",
        action="store_true",
        help="Deprecated compatibility flag. Rebuilds are versioned and non-destructive.",
    )
    rebuild_doc.add_argument(
        "--ingestion-epoch",
        default=None,
        help="Optional epoch identifier to stamp on replacement trees/nodes.",
    )

    rebuild_by_label = subcommands.add_parser("rebuild-by-label")
    rebuild_by_label.add_argument("--label", required=True)
    rebuild_by_label.add_argument("--limit", type=int, default=None)
    rebuild_by_label.add_argument(
        "--force-replace",
        action="store_true",
        help="Deprecated compatibility flag. Rebuilds are versioned and non-destructive.",
    )
    rebuild_by_label.add_argument(
        "--ingestion-epoch",
        default=None,
        help="Optional epoch identifier to stamp on each replacement tree/node set.",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    db = get_database(config.mongo)

    if args.command == "db-ping":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "database": config.mongo.database}))
        return

    if args.command == "backfill-source-metadata":
        ensure_indexes(db)
        updated = []
        skipped = []
        for document in db.documents.find({"source.checksum_sha256": {"$exists": False}}):
            source_path = Path(document["source"]["path"])
            if not source_path.exists():
                skipped.append(
                    {
                        "document_id": str(document["_id"]),
                        "reason": "source_missing",
                        "path": str(source_path),
                    }
                )
                continue
            checksum = sha256_file(source_path)
            archived_path = archive_source(source_path, config.paths.archive, checksum)
            db.documents.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "source.checksum_sha256": checksum,
                        "source.archive_path": str(archived_path),
                    }
                },
            )
            updated.append(
                {
                    "document_id": str(document["_id"]),
                    "checksum_sha256": checksum,
                    "archive_path": str(archived_path),
                }
            )
        print(json.dumps({"ok": True, "updated": updated, "skipped": skipped}, indent=2))
        return

    if args.command == "backfill-schema-metadata":
        ensure_indexes(db)
        print(json.dumps({"ok": True, **backfill_schema_metadata(db)}, indent=2))
        return

    if args.command == "graph-status":
        ensure_indexes(db)
        print(json.dumps({"ok": True, **graph_edge_status(db, limit=args.limit)}, indent=2))
        return

    if args.command == "backfill-structural-graph-edges":
        ensure_indexes(db)
        print(
            json.dumps(
                {"ok": True, **backfill_structural_graph_edges(db, limit=args.limit)},
                indent=2,
            )
        )
        return

    if args.command == "enqueue-inbox":
        ensure_indexes(db)
        jobs = []
        for path in discover_sources(config.paths.ingest):
            checksum = sha256_file(path)
            job = enqueue_source(db, path, checksum)
            dead_letter_path = None
            if job["status"] == "rejected" and path.exists():
                dead_letter_path = move_request_file(path, config.paths.dead_letter / "duplicate", checksum)
                db.queue.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"details.dead_letter_path": str(dead_letter_path)}},
                )
            jobs.append(
                {
                    "job_id": str(job["_id"]),
                    "path": job["path"],
                    "checksum_sha256": job["checksum_sha256"],
                    "status": job["status"],
                    "reason": job.get("reason"),
                    "dead_letter_path": str(dead_letter_path) if dead_letter_path else None,
                    "existing_document_id": str(job["existing_document_id"])
                    if job.get("existing_document_id")
                    else None,
                }
            )
        print(json.dumps({"ok": True, "enqueued": jobs}, indent=2))
        return

    if args.command == "process-next":
        ensure_indexes(db)
        print(json.dumps(process_next(db, config), indent=2))
        return

    if args.command == "queue-status":
        ensure_indexes(db)
        summary = queue_summary(db)
        if summary["oldest_pending"]:
            summary["oldest_pending"]["_id"] = str(summary["oldest_pending"]["_id"])
            summary["oldest_pending"]["created_at"] = summary["oldest_pending"][
                "created_at"
            ].isoformat()
        print(json.dumps({"ok": True, **summary}, indent=2))
        return

    if args.command == "labels":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "labels": label_definitions(db)}, indent=2))
        return

    if args.command == "agent-identities":
        ensure_indexes(db)
        print(
            json.dumps(
                {"ok": True, "identities": list_agent_identities(db, limit=args.limit)},
                indent=2,
            )
        )
        return

    if args.command == "agent-identity":
        ensure_indexes(db)
        identity = get_agent_identity(db, args.identity_id)
        print(json.dumps({"ok": identity is not None, "identity": identity}, indent=2))
        return

    if args.command == "trust-weighting-profiles":
        ensure_indexes(db)
        print(
            json.dumps(
                {"ok": True, "profiles": list_trust_weighting_profiles(db, limit=args.limit)},
                indent=2,
            )
        )
        return

    if args.command == "trust-weighting-profile":
        ensure_indexes(db)
        profile = get_trust_weighting_profile(db, args.weighting_profile_id)
        print(json.dumps({"ok": profile is not None, "profile": profile}, indent=2))
        return

    if args.command == "trust-diagnostic":
        ensure_indexes(db)
        diagnostic = trust_temporal_diagnostic_for_node(
            db,
            args.node_id,
            weighting_profile_id=args.profile_id,
        )
        print(json.dumps({"ok": diagnostic is not None, "result": diagnostic}, indent=2))
        return

    if args.command == "governance-policies":
        ensure_indexes(db)
        print(
            json.dumps(
                {"ok": True, "policies": list_governance_policies(db, limit=args.limit)},
                indent=2,
            )
        )
        return

    if args.command == "governance-policy":
        ensure_indexes(db)
        policy = get_governance_policy(db, args.policy_id)
        print(json.dumps({"ok": policy is not None, "policy": policy}, indent=2))
        return

    if args.command == "process-objects":
        ensure_indexes(db)
        print(
            json.dumps(
                {"ok": True, "processes": list_process_objects(db, limit=args.limit)},
                indent=2,
            )
        )
        return

    if args.command == "process-object":
        ensure_indexes(db)
        process = get_process_object(db, args.process_id)
        print(json.dumps({"ok": process is not None, "process": process}, indent=2))
        return

    if args.command == "process-runs":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "runs": list_process_runs(
                        db,
                        session_id=args.session_id,
                        status=args.status,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "process-run":
        ensure_indexes(db)
        run = get_process_run(db, args.run_id)
        print(json.dumps({"ok": run is not None, "run": run}, indent=2))
        return

    if args.command == "start-process-run":
        ensure_indexes(db)
        run = create_process_run(
            db,
            process_id=args.process_id,
            session_id=args.session_id,
            identity_id=args.identity_id,
            current_step_id=args.current_step_id,
            status=args.status,
        )
        print(json.dumps({"ok": True, "run": run}, indent=2))
        return

    if args.command == "update-process-run":
        ensure_indexes(db)
        exception = None
        if args.exception_reason or args.exception_proposal:
            exception = {
                "reason": args.exception_reason,
                "proposal": args.exception_proposal,
                "reviewer": args.exception_reviewer,
                "note": args.exception_note,
            }
        run = update_process_run(
            db,
            args.run_id,
            status=args.status,
            current_step_id=args.current_step_id,
            completed_step_id=args.completed_step_id,
            exchange_id=args.exchange_id,
            exception=exception,
        )
        print(json.dumps({"ok": run is not None, "run": run}, indent=2))
        return

    if args.command == "sessions":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "sessions": list_sessions(db)}, indent=2))
        return

    if args.command == "active-documents":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "session_id": args.session_id,
                    "documents": list_active_documents(
                        db,
                        session_id=args.session_id,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "output-ingestion":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "jobs": list_output_ingestion_jobs(
                        db,
                        limit=args.limit,
                        status=args.status,
                        session_id=args.session_id,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "process-output-ingestion":
        ensure_indexes(db)
        print(json.dumps(process_next_output_ingestion(db), indent=2))
        return

    if args.command == "review-generated-output":
        ensure_indexes(db)
        try:
            nodes = list_generated_output_nodes(
                db,
                limit=args.limit,
                endorsement_label=args.endorsement,
            )
        except ValueError as error:
            print(
                json.dumps(
                    {"ok": False, "reason": "invalid_endorsement_label", "error": str(error)},
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                {
                    "ok": True,
                    "nodes": nodes,
                },
                indent=2,
            )
        )
        return

    if args.command == "endorse-node":
        ensure_indexes(db)
        print(
            json.dumps(
                update_node_endorsement(
                    db,
                    node_id=args.node_id,
                    endorsement_label=args.endorsement,
                    reviewer=args.reviewer,
                    note=args.note,
                ),
                indent=2,
            )
        )
        return

    if args.command == "create-session":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "session": create_session(
                        db,
                        title=args.title,
                        session_id=args.session_id,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "list-docs":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "documents": list_documents(db, args.limit)}, indent=2))
        return

    if args.command == "show-doc":
        ensure_indexes(db)
        document = get_document(db, args.document_id)
        print(json.dumps({"ok": document is not None, "document": document}, indent=2))
        return

    if args.command == "search-nodes":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "nodes": search_nodes(
                        db,
                        query=args.query,
                        label=args.label,
                        endorsement_label=args.endorsement,
                        document_id=args.document_id,
                        created_after=parse_iso_datetime(args.created_after),
                        created_before=parse_iso_datetime(args.created_before),
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "node-context":
        ensure_indexes(db)
        context_result = node_context(db, args.node_id, child_limit=args.child_limit)
        print(json.dumps({"ok": context_result is not None, "context": context_result}, indent=2))
        return

    if args.command == "graph-edges":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "edges": graph_edges_for_node(
                        db,
                        args.node_id,
                        direction=args.direction,
                        relation_type=args.relation_type,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "expand-proximity":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "nodes": expand_proximity(
                        db,
                        args.node_id,
                        direction=args.direction,
                        relation_type=args.relation_type,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "expand-graph-paths":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "paths": expand_graph_paths(
                        db,
                        args.node_id,
                        direction=args.direction,
                        relation_type=args.relation_type,
                        max_depth=args.max_depth,
                        branch_limit=args.branch_limit,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "semantic-candidates":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "nodes": semantic_candidate_nodes(
                        db,
                        args.node_id,
                        include_same_document=args.include_same_document,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "enqueue-semantic-candidates":
        ensure_indexes(db)
        print(
            json.dumps(
                enqueue_semantic_edge_candidates(
                    db,
                    node_id=args.node_id,
                    include_same_document=args.include_same_document,
                    relation_type=args.relation_type,
                    created_by=args.created_by,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
        return

    if args.command == "semantic-edge-candidates":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "candidates": list_semantic_edge_candidates(
                        db,
                        status=args.status,
                        limit=args.limit,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "review-semantic-edge-candidate":
        ensure_indexes(db)
        print(
            json.dumps(
                review_semantic_edge_candidate(
                    db,
                    candidate_id=args.candidate_id,
                    action=args.action,
                    reviewer=args.reviewer,
                    note=args.note,
                    weight=args.weight,
                    confidence=args.confidence,
                ),
                indent=2,
            )
        )
        return

    if args.command == "create-semantic-edge":
        ensure_indexes(db)
        print(
            json.dumps(
                create_reviewed_semantic_edge(
                    db,
                    source_node_id=args.source_node_id,
                    target_node_id=args.target_node_id,
                    relation_type=args.relation_type,
                    weight=args.weight,
                    confidence=args.confidence,
                    reviewer=args.reviewer,
                    note=args.note,
                ),
                indent=2,
            )
        )
        return

    if args.command == "compile-context":
        ensure_indexes(db)
        context_result = compile_context(
            db,
            args.node_id,
            ancestor_depth=args.ancestor_depth,
            sibling_window=args.sibling_window,
            child_depth=args.child_depth,
            child_limit=args.child_limit,
        )
        print(json.dumps({"ok": context_result is not None, "context": context_result}, indent=2))
        return

    if args.command == "render-context":
        ensure_indexes(db)
        context_result = compile_context(
            db,
            args.node_id,
            ancestor_depth=args.ancestor_depth,
            sibling_window=args.sibling_window,
            child_depth=args.child_depth,
            child_limit=args.child_limit,
        )
        if not context_result:
            print(json.dumps({"ok": False, "context": None}, indent=2))
            return
        rendered = render_context_document(
            context_result,
            char_budget=args.char_budget or config.retrieval.context_char_budget,
        )
        if args.json:
            print(json.dumps({"ok": True, "rendered": rendered}, indent=2))
        else:
            print(rendered["text"])
        return

    if args.command == "build-prompt":
        ensure_indexes(db)
        context_result = compile_context(
            db,
            args.node_id,
            ancestor_depth=args.ancestor_depth,
            sibling_window=args.sibling_window,
            child_depth=args.child_depth,
            child_limit=args.child_limit,
        )
        if not context_result:
            print(json.dumps({"ok": False, "prompt": None}, indent=2))
            return
        envelope = build_prompt_envelope(
            context_result,
            query=args.query,
            system_instruction=args.system_instruction,
            token_budget=args.token_budget or config.retrieval.prompt_token_budget,
            reserved_response_tokens=(
                args.reserved_response_tokens or config.retrieval.reserved_response_tokens
            ),
        )
        if args.text:
            print(envelope["prompt_text"])
        else:
            print(json.dumps({"ok": True, "prompt": envelope}, indent=2))
        return

    if args.command == "ask":
        ensure_indexes(db)
        result = answer_query(
            db,
            config,
            query=args.query,
            focus_node_id=args.node_id,
            session_id=args.session_id,
            answer_adapter_name=args.adapter,
            ollama_model=args.model,
            retrieval_mode=args.retrieval_mode,
        )
        if args.json or not result.get("ok"):
            print(json.dumps(result, indent=2))
        else:
            print(result["answer"])
            print(f"\nexchange_id: {result['exchange_id']}")
        return

    if args.command == "chat":
        ensure_indexes(db)
        print("Mnemosyne chat. Type /exit to quit.")
        while True:
            try:
                query = input("> ").strip()
            except EOFError:
                break
            if not query:
                continue
            if query in {"/exit", "/quit"}:
                break
            result = answer_query(
                db,
                config,
                query=query,
                focus_node_id=args.node_id,
                session_id=args.session_id,
                answer_adapter_name=args.adapter,
                ollama_model=args.model,
                retrieval_mode=args.retrieval_mode,
            )
            if not result.get("ok"):
                print(json.dumps(result, indent=2))
            else:
                print(result["answer"])
                print(f"[exchange_id: {result['exchange_id']}]\n")
        return

    if args.command == "history":
        ensure_indexes(db)
        print(
            json.dumps(
                {
                    "ok": True,
                    "exchanges": recent_exchanges(
                        db,
                        limit=args.limit,
                        session_id=args.session_id,
                        query_text=args.query,
                        adapter=args.adapter,
                        model=args.model,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command == "show-tree":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "nodes": document_tree(db, args.document_id)}, indent=2))
        return

    if args.command == "queue-recent":
        ensure_indexes(db)
        jobs = []
        for job in recent_jobs(
            db,
            limit=args.limit,
            status=args.status,
            query_text=args.query,
            reason=args.reason,
        ):
            job["_id"] = str(job["_id"])
            if job.get("existing_document_id"):
                job["existing_document_id"] = str(job["existing_document_id"])
            if job.get("existing_queue_id"):
                job["existing_queue_id"] = str(job["existing_queue_id"])
            for field in ("created_at", "updated_at"):
                if job.get(field):
                    job[field] = job[field].isoformat()
            jobs.append(job)
        print(json.dumps({"ok": True, "jobs": jobs}, indent=2))
        return

    if args.command == "process-inbox":
        ensure_indexes(db)
        enqueued = []
        for path in discover_sources(config.paths.ingest):
            checksum = sha256_file(path)
            job = enqueue_source(db, path, checksum)
            dead_letter_path = None
            if job["status"] == "rejected" and path.exists():
                dead_letter_path = move_request_file(path, config.paths.dead_letter / "duplicate", checksum)
                db.queue.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"details.dead_letter_path": str(dead_letter_path)}},
                )
            enqueued.append(
                {
                    "job_id": str(job["_id"]),
                    "path": job["path"],
                    "status": job["status"],
                    "reason": job.get("reason"),
                    "dead_letter_path": str(dead_letter_path) if dead_letter_path else None,
                }
            )
        processed = []
        while True:
            result = process_next(db, config)
            if result["status"] == "idle":
                break
            processed.append(result)
        print(json.dumps({"ok": True, "enqueued": enqueued, "processed": processed}, indent=2))
        return

    if args.command == "ingest-one":
        ensure_indexes(db)
        print(
            json.dumps(
                ingest_source_path(
                    db,
                    config,
                    Path(args.path),
                    args.label,
                    ingestion_epoch=args.ingestion_epoch,
                ),
                indent=2,
            )
        )
        return

    if args.command == "ingest-folder":
        ensure_indexes(db)
        root = Path(args.path)
        results = []
        source_plan = chronological_folder_source_plan(root)
        if args.limit is not None:
            source_plan = source_plan[: args.limit]
        for item in source_plan:
            path = item["path"]
            if item.get("error"):
                results.append(
                    {
                        "ok": False,
                        "path": str(path),
                        "status": "rejected",
                        "reason": "source_unreadable",
                        "error": item["error"],
                        "message": item.get("message"),
                    }
                )
                continue
            results.append(
                ingest_source_path(
                    db,
                    config,
                    path,
                    args.label,
                    ingestion_epoch=args.ingestion_epoch,
                )
            )
        rejected = [result for result in results if not result.get("ok")]
        output = {
            "ok": True,
            "root": str(root),
            "ingestion_epoch": args.ingestion_epoch,
            "ordering": "origin_date_then_path",
            "file_count": len(source_plan),
            "inserted": sum(1 for result in results if result.get("ok")),
            "rejected": len(rejected),
            "rejection_reasons": rejection_reason_counts(rejected),
            "source_order": [
                {
                    "path": str(item["path"]),
                    "origin_date": item.get("origin_date"),
                    "origin_date_source": item.get("origin_date_source"),
                    "error": item.get("error"),
                }
                for item in source_plan[:20]
            ],
        }
        if args.include_results:
            output["results"] = results
        print(
            json.dumps(output, indent=2)
        )
        return

    if args.command == "rebuild-document":
        ensure_indexes(db)
        print(
            json.dumps(
                rebuild_document_from_existing_source(
                    db,
                    args.document_id,
                    args.source,
                    ingestion_epoch=args.ingestion_epoch,
                ),
                indent=2,
            )
        )
        return

    if args.command == "rebuild-by-label":
        ensure_indexes(db)
        document_ids = document_ids_for_label(db, args.label)
        if args.limit is not None:
            document_ids = document_ids[: args.limit]
        results = [
            rebuild_document_from_existing_source(
                db,
                document_id,
                ingestion_epoch=args.ingestion_epoch,
            )
            for document_id in document_ids
        ]
        failures = [result for result in results if not result.get("ok")]
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "label": args.label,
                    "document_count": len(document_ids),
                    "rebuilt": sum(1 for result in results if result.get("ok")),
                    "failed": len(failures),
                    "failures": failures,
                },
                indent=2,
            )
        )
        return

    raise SystemExit(f"Unknown command: {args.command}")
