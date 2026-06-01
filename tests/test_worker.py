from pathlib import Path

from bson import ObjectId

from mnemosyne.config import AppConfig
from mnemosyne.ingestion.worker import discover_sources, process_next

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


def test_process_next_records_completed_process_run(monkeypatch, tmp_path: Path) -> None:
    import mnemosyne.ingestion.worker as worker

    updates = []
    completed_jobs = []
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nText.", encoding="utf-8")

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": ObjectId(),
            "path": str(source),
            "checksum_sha256": "abc123",
            "attempts": 0,
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
        lambda _db, _job_id, inserted: completed_jobs.append(inserted),
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
    assert result["process_run_id"] == "run1"
    assert result["activity_report"]["kind"] == "ingestion_activity_report"
    assert result["activity_report"]["semantic_processing"]["adapter"] == "mock"
    assert "Ingestion Activity Log" in result["activity_log"]
    assert "Repository write: document doc1" in result["activity_log"]
    assert completed_jobs[0]["activity_log"] == result["activity_log"]
    assert completed_jobs[0]["activity_report"]["kind"] == "ingestion_activity_report"
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
    import mnemosyne.ingestion.worker as worker

    updates = []
    missing = tmp_path / "missing.md"

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": ObjectId(),
            "path": str(missing),
            "checksum_sha256": "abc123",
            "attempts": 0,
        },
    )
    monkeypatch.setattr(worker, "fail_job", lambda *_args, **_kwargs: None)
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
    assert result["process_run_id"] == "run1"
    assert result["activity_report"]["outcome"]["reason"] == "source_missing"
    assert "Restore source file and retry." in result["activity_log"]
    assert updates[0]["status"] == "blocked"
    assert updates[0]["current_step_id"] == "source_missing"
    assert updates[0]["exception"]["reason"] == "source_missing"


def test_process_next_persists_embeddings_through_worker_ingestion(monkeypatch, tmp_path: Path) -> None:
    import mnemosyne.ingestion.worker as worker

    source = tmp_path / "source.md"
    source.write_text("# Source\n\nFirst paragraph.\n\nSecond paragraph.", encoding="utf-8")
    db = RepoFakeDb()

    monkeypatch.setattr(
        worker,
        "claim_next_pending",
        lambda _db: {
            "_id": ObjectId(),
            "path": str(source),
            "checksum_sha256": "worker-checksum",
            "attempts": 0,
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
    assert result["embedded_node_count"] == len(db.nodes.rows)
    assert db.nodes.rows, "expected the worker path to insert nodes"
    for row in db.nodes.rows:
        embedding = row["embedding"]
        assert embedding["adapter"] == "mock_embedding"
        assert embedding["dimensions"] == 16
        assert len(embedding["vector"]) == 16
        assert embedding["source_text_hash"].startswith("sha256:")
    assert "node(s) embedded" in result["activity_log"]


class FakeDb:
    pass
