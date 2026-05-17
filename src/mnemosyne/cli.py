from __future__ import annotations

import argparse
import json
from pathlib import Path

from mnemosyne.adapters.mock import MockIngestionAdapter
from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.db.indexes import ensure_indexes
from mnemosyne.db.repositories import (
    DuplicateSourceError,
    backfill_schema_metadata,
    commit_ingestion,
    find_duplicate_by_checksum,
    document_tree,
    label_definitions,
)
from mnemosyne.db.queue import enqueue_source, queue_summary, recent_jobs
from mnemosyne.ingestion.files import archive_source, move_request_file, sha256_file
from mnemosyne.ingestion.parser import read_text_source
from mnemosyne.ingestion.worker import discover_sources, process_next
from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    compile_context,
    get_document,
    list_documents,
    node_context,
    parse_iso_datetime,
    render_context_document,
    search_nodes,
)
from mnemosyne.sessions.exchanges import recent_exchanges
from mnemosyne.sessions.interaction import answer_query
from mnemosyne.sessions.registry import create_session, list_sessions


def main() -> None:
    parser = argparse.ArgumentParser(prog="mnemosyne")
    parser.add_argument("--config", default="config.yaml")

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("db-ping")
    subcommands.add_parser("backfill-source-metadata")
    subcommands.add_parser("backfill-schema-metadata")
    subcommands.add_parser("enqueue-inbox")
    subcommands.add_parser("process-next")
    subcommands.add_parser("process-inbox")
    subcommands.add_parser("queue-status")
    subcommands.add_parser("labels")
    subcommands.add_parser("sessions")

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
    ask.add_argument("--json", action="store_true")

    chat = subcommands.add_parser("chat")
    chat.add_argument("--node-id", default=None)
    chat.add_argument("--session-id", default="default")
    chat.add_argument("--adapter", default=None)
    chat.add_argument("--model", default=None)

    history = subcommands.add_parser("history")
    history.add_argument("--session-id", default=None)
    history.add_argument("--limit", type=int, default=10)

    queue_recent = subcommands.add_parser("queue-recent")
    queue_recent.add_argument("--limit", type=int, default=10)
    queue_recent.add_argument("--status", default=None)

    show_tree = subcommands.add_parser("show-tree")
    show_tree.add_argument("document_id")

    ingest_one = subcommands.add_parser("ingest-one")
    ingest_one.add_argument("path")

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

    if args.command == "sessions":
        ensure_indexes(db)
        print(json.dumps({"ok": True, "sessions": list_sessions(db)}, indent=2))
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
        for job in recent_jobs(db, limit=args.limit, status=args.status):
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
        path = Path(args.path)
        checksum = sha256_file(path)
        duplicate = find_duplicate_by_checksum(db, checksum)
        if duplicate:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "status": "rejected",
                        "reason": "duplicate_checksum",
                        "checksum_sha256": checksum,
                        "existing_document_id": str(duplicate["_id"]),
                        "message": "File rejected because identical content has already been ingested.",
                    },
                    indent=2,
                )
            )
            return

        text, source_kind = read_text_source(path)
        result = MockIngestionAdapter().process(path, text, source_kind)
        archived_path = archive_source(path, config.paths.archive, checksum)
        result.source.checksum_sha256 = checksum
        result.source.archive_path = str(archived_path)
        try:
            inserted = commit_ingestion(db, result)
        except DuplicateSourceError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "status": "rejected",
                        "reason": "duplicate_checksum",
                        "checksum_sha256": error.checksum,
                        "existing_document_id": str(error.existing_document_id),
                        "message": "File rejected because identical content has already been ingested.",
                    },
                    indent=2,
                )
            )
            return
        inserted["ok"] = True
        inserted["archive_path"] = str(archived_path)
        inserted["checksum_sha256"] = checksum
        print(json.dumps(inserted, indent=2))
        return

    raise SystemExit(f"Unknown command: {args.command}")
