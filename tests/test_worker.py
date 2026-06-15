from pathlib import Path

import pytest
from bson import ObjectId

from tirzah.config import AppConfig
from tirzah.db.repositories import DuplicateSourceError
from tirzah.ingestion.activity import ingestion_activity_fields, ingestion_activity_report
from tirzah.ingestion.worker import activity_for_worker_failure, discover_sources, process_next

from test_repositories import FakeDb as RepoFakeDb


def test_discover_sources_only_returns_supported_files(tmp_path: Path) -> None:
    markdown = tmp_path / "a.md"
    text = tmp_path / "b.txt"
    unsupported = tmp_path / "c.pdf"
    markdown.write_text("a", encoding="utf-8")
    text.write_text("b", encoding="utf-8")
    unsupported.write_text("c", encoding="utf-8")

    assert discover_sources(tmp_path) == [markdown, text]


def test_discover_sources_handles_missing_folder(tmp_path: Path) -> None:
    assert discover_sources(tmp_path / "missing") == []


def test_activity_for_worker_failure_matches_activity_fields(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    details = {"path": str(source)}
    report = ingestion_activity_report(
        path=source,
        status="failed",
        checksum_sha256="abc123",
        job_id="job1",
        process_run_id="run1",
        reason="source_missing",
        message="Restore source file and retry.",
        details=details,
    )

    fields = activity_for_worker_failure(
        path=source,
        status="failed",
        checksum_sha256="abc123",
        job_id="job1",
        process_run_id="run1",
        reason="source_missing",
        message="Restore source file and retry.",
        details=details,
    )

    assert fields == ingestion_activity_fields(report)


def test_process_next_records_completed_process_run(monkeypatch, tmp_path: Path) -> None:
    import tirzah.ingestion.worker as worker

    updates = []
    completed_jobs = []
    job_id = ObjectId()
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nText.", encoding="utf-8")

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(source),
            "checksum_sha256": "abc123",
            "attempts": 1,
        },
    )
    monkeypatch.setattr(worker, "archive_source", lambda path, archive_dir, checksum: tmp_path / "archive.md")
    monkeypatch.setattr(worker, "move_request_file", lambda path, destination, checksum: tmp_path / "processed.md")
    monkeypatch.setattr(
        worker,
        "commit_ingestion",
        lambda _db, result, embedder=None: {"document_id": "doc1", "tree_id": "tree1", "node_ids": ["node1"]},
    )
    monkeypatch.setattr(
        worker,
        "complete_job",
        lambda _db, _job_id, inserted: completed_jobs.append(
            {"job_id": _job_id, "inserted": inserted}
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )
    monkeypatch.setattr(
        worker,
        "update_process_run",
        lambda _db, run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}),
    )

    result = process_next(FakeDb(), AppConfig())

    assert result["ok"] is True
    assert result["job_id"] == str(job_id)
    assert result["process_run_id"] == "run1"
    assert result["activity_report"]["kind"] == "ingestion_activity_report"
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["activity_report"]["semantic_processing"]["adapter"] == "mock"
    assert "Ingestion Activity Log" in result["activity_log"]
    assert "Repository write: document doc1" in result["activity_log"]
    assert completed_jobs[0]["job_id"] == job_id
    assert completed_jobs[0]["inserted"]["activity_log"] == result["activity_log"]
    assert completed_jobs[0]["inserted"]["activity_report"]["kind"] == "ingestion_activity_report"
    assert updates == [
        {
            "run_id": "run1",
            "status": "completed",
            "current_step_id": "ingestion_committed",
            "completed_step_id": "commit_ingestion",
            "exception": None,
        }
    ]


def test_process_next_marks_process_run_blocked_when_source_missing(monkeypatch, tmp_path: Path) -> None:
    import tirzah.ingestion.worker as worker

    updates = []
    failed_jobs = []
    job_id = ObjectId()
    missing = tmp_path / "missing.md"

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(missing),
            "checksum_sha256": "abc123",
            "attempts": 1,
        },
    )
    monkeypatch.setattr(
        worker,
        "fail_job",
        lambda _db, _job_id, reason, details: failed_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )
    monkeypatch.setattr(
        worker,
        "update_process_run",
        lambda _db, run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}),
    )

    result = process_next(FakeDb(), AppConfig())

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["job_id"] == str(job_id)
    assert result["process_run_id"] == "run1"
    assert result["activity_report"]["kind"] == "ingestion_activity_report"
    assert result["activity_report"]["status"] == "failed"
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["activity_report"]["outcome"]["reason"] == "source_missing"
    assert result["activity_report"]["outcome"]["details"] == {"path": str(missing)}
    assert result["activity_log"].startswith("Ingestion Activity Log")
    assert "Outcome reason: source_missing." in result["activity_log"]
    assert "Restore source file and retry." in result["activity_log"]
    assert failed_jobs == [
        {
            "job_id": job_id,
            "reason": "source_missing",
            "details": {"path": str(missing)},
        }
    ]
    assert updates[0]["status"] == "blocked"
    assert updates[0]["current_step_id"] == "source_missing"
    assert updates[0]["exception"]["reason"] == "source_missing"


def test_process_next_rejects_duplicate_with_activity_fields(monkeypatch, tmp_path: Path) -> None:
    import tirzah.ingestion.worker as worker

    updates = []
    rejected_jobs = []
    job_id = ObjectId()
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nText.", encoding="utf-8")
    existing_document_id = ObjectId()

    def raise_duplicate(_db, _result, embedder=None):
        raise DuplicateSourceError("commit-checksum", existing_document_id)

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(source),
            "checksum_sha256": "abc123",
            "attempts": 1,
        },
    )
    monkeypatch.setattr(worker, "archive_source", lambda path, archive_dir, checksum: tmp_path / "archive.md")
    monkeypatch.setattr(
        worker,
        "move_request_file",
        lambda path, destination, checksum: tmp_path / "duplicate.md",
    )
    monkeypatch.setattr(worker, "embedding_adapter", lambda _runtime: "embedder")
    monkeypatch.setattr(worker, "commit_ingestion", raise_duplicate)
    monkeypatch.setattr(
        worker,
        "reject_job",
        lambda _db, _job_id, reason, details: rejected_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )
    monkeypatch.setattr(
        worker,
        "update_process_run",
        lambda _db, run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}),
    )

    result = process_next(FakeDb(), AppConfig())

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["job_id"] == str(job_id)
    assert result["process_run_id"] == "run1"
    assert result["checksum_sha256"] == "commit-checksum"
    assert result["existing_document_id"] == str(existing_document_id)
    assert result["activity_report"]["status"] == "rejected"
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["activity_report"]["outcome"]["reason"] == "duplicate_checksum"
    assert result["activity_report"]["outcome"]["details"]["dead_letter_path"].endswith(
        "duplicate.md"
    )
    assert "Semantic processing: mock generated" in result["activity_log"]
    assert "Existing document:" in result["activity_log"]
    assert rejected_jobs == [
        {
            "job_id": job_id,
            "reason": "duplicate_checksum",
            "details": {
                "checksum_sha256": "commit-checksum",
                "existing_document_id": str(existing_document_id),
                "dead_letter_path": str(tmp_path / "duplicate.md"),
            },
        }
    ]
    assert updates[0]["status"] == "blocked"
    assert updates[0]["current_step_id"] == "duplicate_rejected"
    assert updates[0]["exception"]["reason"] == "duplicate_checksum"


@pytest.mark.parametrize("attempts", [1, 2])
def test_process_next_retries_transient_error_with_activity_fields(
    monkeypatch,
    tmp_path: Path,
    attempts: int,
) -> None:
    import tirzah.ingestion.worker as worker

    updates = []
    failed_jobs = []
    retried_jobs = []
    job_id = ObjectId()
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nText.", encoding="utf-8")

    def raise_runtime(_path):
        raise RuntimeError("adapter unavailable")

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(source),
            "checksum_sha256": "abc123",
            "attempts": attempts,
        },
    )
    monkeypatch.setattr(worker, "read_text_source", raise_runtime)
    monkeypatch.setattr(
        worker,
        "retry_job",
        lambda _db, _job_id, reason, details: retried_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "fail_job",
        lambda _db, _job_id, reason, details: failed_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )
    monkeypatch.setattr(
        worker,
        "update_process_run",
        lambda _db, run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}),
    )

    result = process_next(FakeDb(), AppConfig())

    assert result["ok"] is False
    assert result["status"] == "retrying"
    assert result["job_id"] == str(job_id)
    assert result["attempts"] == attempts
    assert result["max_attempts"] == 3
    assert result["activity_report"]["status"] == "retrying"
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["activity_report"]["outcome"]["reason"] == "RuntimeError"
    assert "Retry ingestion after transient failure." in result["activity_log"]
    assert retried_jobs == [
        {
            "job_id": job_id,
            "reason": "RuntimeError",
            "details": {"path": str(source), "error": "adapter unavailable"},
        }
    ]
    assert failed_jobs == []
    assert updates[0]["status"] == "active"
    assert updates[0]["current_step_id"] == "retrying"
    assert updates[0]["exception"]["reason"] == "RuntimeError"


def test_process_next_fails_terminal_error_with_activity_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import tirzah.ingestion.worker as worker

    updates = []
    failed_jobs = []
    retried_jobs = []
    job_id = ObjectId()
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nText.", encoding="utf-8")

    def raise_value_error(_path):
        raise ValueError("bad source")

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(source),
            "checksum_sha256": "abc123",
            "attempts": 3,
        },
    )
    monkeypatch.setattr(worker, "read_text_source", raise_value_error)
    monkeypatch.setattr(
        worker,
        "retry_job",
        lambda _db, _job_id, reason, details: retried_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "move_request_file",
        lambda path, destination, checksum: tmp_path / "failed.md",
    )
    monkeypatch.setattr(
        worker,
        "fail_job",
        lambda _db, _job_id, reason, details: failed_jobs.append(
            {"job_id": _job_id, "reason": reason, "details": details}
        ),
    )
    monkeypatch.setattr(
        worker,
        "create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )
    monkeypatch.setattr(
        worker,
        "update_process_run",
        lambda _db, run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}),
    )

    result = process_next(FakeDb(), AppConfig())

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["job_id"] == str(job_id)
    assert result["dead_letter_path"] == str(tmp_path / "failed.md")
    assert result["activity_report"]["status"] == "failed"
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["activity_report"]["outcome"]["reason"] == "ValueError"
    assert "Inspect failed source and retry if appropriate." in result["activity_log"]
    assert failed_jobs == [
        {
            "job_id": job_id,
            "reason": "ValueError",
            "details": {
                "path": str(source),
                "error": "bad source",
                "dead_letter_path": str(tmp_path / "failed.md"),
            },
        }
    ]
    assert retried_jobs == []
    assert updates[0]["status"] == "blocked"
    assert updates[0]["current_step_id"] == "failed"
    assert updates[0]["exception"]["reason"] == "ValueError"


def test_process_next_persists_embeddings_through_worker_ingestion(monkeypatch, tmp_path: Path) -> None:
    import tirzah.ingestion.worker as worker

    source = tmp_path / "source.md"
    source.write_text("# Source\n\nFirst paragraph.\n\nSecond paragraph.", encoding="utf-8")
    db = RepoFakeDb()
    job_id = ObjectId()

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": job_id,
            "path": str(source),
            "checksum_sha256": "worker-checksum",
            "attempts": 1,
        },
    )
    monkeypatch.setattr(worker, "archive_source", lambda path, archive_dir, checksum: tmp_path / "archive.md")
    monkeypatch.setattr(worker, "move_request_file", lambda path, destination, checksum: tmp_path / "processed.md")
    monkeypatch.setattr(worker, "complete_job", lambda _db, _job_id, inserted: None)
    monkeypatch.setattr(worker, "create_process_run", lambda _db, **kwargs: {"run_id": "run1", **kwargs})
    monkeypatch.setattr(worker, "update_process_run", lambda _db, run_id, **kwargs: None)

    result = process_next(db, AppConfig())

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["job_id"] == str(job_id)
    assert result["activity_report"]["queue"]["job_id"] == str(job_id)
    assert result["embedded_node_count"] == len(db.nodes.rows)
    assert db.nodes.rows, "expected the worker path to insert nodes"
    for row in db.nodes.rows:
        embedding = row["embedding"]
        assert embedding["adapter"] == "mock_embedding"
        assert embedding["dimensions"] == 16
        assert len(embedding["vector"]) == 16
        assert embedding["source_text_hash"].startswith("sha256:")
    assert "node(s) profiled" in result["activity_log"]


class FakeDb:
    pass
