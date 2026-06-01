from __future__ import annotations

from pathlib import Path
from typing import Any

from pymongo.database import Database

from mnemosyne.adapters.mock import MockIngestionAdapter
from mnemosyne.config import AppConfig
from mnemosyne.db.governance import create_process_run, update_process_run
from mnemosyne.db.queue import claim_next_pending, complete_job, fail_job, reject_job, retry_job
from mnemosyne.db.repositories import DuplicateSourceError, commit_ingestion
from mnemosyne.ingestion.dates import annotate_source_dates
from mnemosyne.ingestion.files import archive_source, move_request_file
from mnemosyne.ingestion.parser import SUPPORTED_SUFFIXES, read_text_source


def discover_sources(ingest_dir: Path) -> list[Path]:
    if not ingest_dir.exists():
        return []
    return sorted(
        path
        for path in ingest_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def process_next(db: Database, config: AppConfig) -> dict[str, Any]:
    job = claim_next_pending(db)
    if not job:
        return {"ok": True, "status": "idle"}

    path = Path(job["path"])
    checksum = job["checksum_sha256"]
    process_run_id = start_ingestion_process_run(db, job)
    if not path.exists():
        details = {"path": str(path)}
        fail_job(db, job["_id"], "source_missing", details)
        finish_ingestion_process_run(
            db,
            process_run_id,
            status="blocked",
            current_step_id="source_missing",
            exception=ingestion_exception_payload("source_missing", "Restore source file and retry.", details),
        )
        return {
            "ok": False,
            "status": "failed",
            "job_id": str(job["_id"]),
            "process_run_id": process_run_id,
            **details,
        }

    try:
        text, source_kind = read_text_source(path)
        result = MockIngestionAdapter().process(path, text, source_kind)
        annotate_source_dates(result, path, text)
        archived_path = archive_source(path, config.paths.archive, checksum)
        result.source.checksum_sha256 = checksum
        result.source.archive_path = str(archived_path)
        inserted = commit_ingestion(db, result)
    except DuplicateSourceError as error:
        dead_letter_path = move_request_file(path, config.paths.dead_letter / "duplicate", checksum)
        details = {
            "checksum_sha256": error.checksum,
            "existing_document_id": str(error.existing_document_id),
            "dead_letter_path": str(dead_letter_path),
        }
        reject_job(db, job["_id"], "duplicate_checksum", details)
        finish_ingestion_process_run(
            db,
            process_run_id,
            status="blocked",
            current_step_id="duplicate_rejected",
            exception=ingestion_exception_payload(
                "duplicate_checksum",
                "Use the existing document or provide a distinct source.",
                details,
            ),
        )
        return {
            "ok": False,
            "status": "rejected",
            "job_id": str(job["_id"]),
            "process_run_id": process_run_id,
            **details,
        }
    except Exception as error:
        details = {"path": str(path), "error": str(error)}
        if job["attempts"] < config.queue.max_attempts:
            retry_job(db, job["_id"], error.__class__.__name__, details)
            finish_ingestion_process_run(
                db,
                process_run_id,
                status="active",
                current_step_id="retrying",
                exception=ingestion_exception_payload(
                    error.__class__.__name__,
                    "Retry ingestion after transient failure.",
                    details,
                ),
            )
            return {
                "ok": False,
                "status": "retrying",
                "job_id": str(job["_id"]),
                "process_run_id": process_run_id,
                "attempts": job["attempts"],
                "max_attempts": config.queue.max_attempts,
                **details,
            }

        dead_letter_path = None
        if path.exists():
            dead_letter_path = move_request_file(path, config.paths.dead_letter / "failed", checksum)
        if dead_letter_path:
            details["dead_letter_path"] = str(dead_letter_path)
        fail_job(db, job["_id"], error.__class__.__name__, details)
        finish_ingestion_process_run(
            db,
            process_run_id,
            status="blocked",
            current_step_id="failed",
            exception=ingestion_exception_payload(
                error.__class__.__name__,
                "Inspect failed source and retry if appropriate.",
                details,
            ),
        )
        return {
            "ok": False,
            "status": "failed",
            "job_id": str(job["_id"]),
            "process_run_id": process_run_id,
            **details,
        }

    processed_path = move_request_file(path, config.paths.staging / "processed", checksum)
    inserted["archive_path"] = str(archived_path)
    inserted["processed_path"] = str(processed_path)
    inserted["checksum_sha256"] = checksum
    complete_job(db, job["_id"], inserted)
    finish_ingestion_process_run(
        db,
        process_run_id,
        status="completed",
        current_step_id="ingestion_committed",
        completed_step_id="commit_ingestion",
    )
    return {
        "ok": True,
        "status": "completed",
        "job_id": str(job["_id"]),
        "process_run_id": process_run_id,
        **inserted,
    }


def start_ingestion_process_run(db: Database, job: dict[str, Any]) -> str | None:
    try:
        run = create_process_run(
            db,
            process_id="ingest_source",
            session_id="ingestion",
            current_step_id="source_claimed",
            status="active",
        )
    except Exception:
        return None
    return run.get("run_id")


def finish_ingestion_process_run(
    db: Database,
    run_id: str | None,
    *,
    status: str,
    current_step_id: str,
    completed_step_id: str | None = None,
    exception: dict[str, Any] | None = None,
) -> None:
    if not run_id:
        return
    try:
        update_process_run(
            db,
            run_id,
            status=status,
            current_step_id=current_step_id,
            completed_step_id=completed_step_id,
            exception=exception,
        )
    except Exception:
        return


def ingestion_exception_payload(
    reason: str,
    proposal: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reason": reason,
        "proposal": proposal,
        "note": str(details),
        "details": details,
    }
