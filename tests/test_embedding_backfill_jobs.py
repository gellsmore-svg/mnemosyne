from datetime import datetime, timezone

from bson import ObjectId

import mnemosyne.ingestion.embedding_backfill as embedding_backfill
from mnemosyne.ingestion.embedding_backfill import (
    create_embedding_backfill_job,
    list_embedding_backfill_jobs,
    process_next_embedding_backfill_job,
)


def test_create_embedding_backfill_job_persists_scope() -> None:
    db = FakeDb()

    job = create_embedding_backfill_job(
        db,
        batch_limit=25,
        label="ams_domain",
        document_id="doc1",
        force=True,
        created_by="test",
    )

    assert job["status"] == "pending"
    assert job["batch_limit"] == 25
    assert job["label"] == "ams_domain"
    assert job["document_id"] == "doc1"
    assert job["force"] is True
    assert job["created_by"] == "test"


def test_process_next_embedding_backfill_job_keeps_full_batch_pending(monkeypatch) -> None:
    db = FakeDb()
    create_embedding_backfill_job(db, batch_limit=2)

    monkeypatch.setattr(
        embedding_backfill,
        "backfill_node_embeddings",
        lambda *_args, **_kwargs: {
            "ok": True,
            "matched_count": 2,
            "updated_count": 2,
            "skipped_count": 0,
            "error_count": 0,
            "last_node_id": "node2",
            "activity_log": "Embedding Backfill Activity Log\n- Status: batch completed.",
        },
    )

    result = process_next_embedding_backfill_job(db, embedder="embedder")

    assert result["status"] == "pending"
    row = db.embedding_backfill_jobs.rows[0]
    assert row["status"] == "pending"
    assert row["batch_count"] == 1
    assert row["updated_count"] == 2
    assert row["last_node_id"] == "node2"
    assert row["last_result"]["activity_log"].startswith("Embedding Backfill Activity Log")


def test_process_next_embedding_backfill_job_completes_partial_batch(monkeypatch) -> None:
    db = FakeDb()
    create_embedding_backfill_job(db, batch_limit=5)

    monkeypatch.setattr(
        embedding_backfill,
        "backfill_node_embeddings",
        lambda *_args, **_kwargs: {
            "ok": True,
            "matched_count": 1,
            "updated_count": 1,
            "skipped_count": 0,
            "error_count": 0,
        },
    )

    result = process_next_embedding_backfill_job(db, embedder="embedder")

    assert result["status"] == "completed"
    assert db.embedding_backfill_jobs.rows[0]["status"] == "completed"


def test_process_next_embedding_backfill_job_keeps_forced_full_batch_pending(monkeypatch) -> None:
    db = FakeDb()
    create_embedding_backfill_job(db, batch_limit=2, force=True)

    monkeypatch.setattr(
        embedding_backfill,
        "backfill_node_embeddings",
        lambda *_args, **_kwargs: {
            "ok": True,
            "matched_count": 2,
            "updated_count": 2,
            "skipped_count": 0,
            "error_count": 0,
            "last_node_id": "node2",
        },
    )

    result = process_next_embedding_backfill_job(db, embedder="embedder")

    assert result["status"] == "pending"
    assert db.embedding_backfill_jobs.rows[0]["status"] == "pending"
    assert db.embedding_backfill_jobs.rows[0]["last_node_id"] == "node2"


def test_process_next_embedding_backfill_job_blocks_on_failure(monkeypatch) -> None:
    db = FakeDb()
    create_embedding_backfill_job(db, batch_limit=5)

    monkeypatch.setattr(
        embedding_backfill,
        "backfill_node_embeddings",
        lambda *_args, **_kwargs: {
            "ok": False,
            "reason": "all_embedding_updates_failed",
            "matched_count": 2,
            "updated_count": 0,
            "skipped_count": 2,
            "error_count": 2,
        },
    )

    result = process_next_embedding_backfill_job(db, embedder="embedder")

    assert result["status"] == "blocked"
    row = db.embedding_backfill_jobs.rows[0]
    assert row["status"] == "blocked"
    assert row["reason"] == "all_embedding_updates_failed"
    assert row["error_count"] == 2


def test_process_next_embedding_backfill_job_blocks_on_exception(monkeypatch) -> None:
    db = FakeDb()
    create_embedding_backfill_job(db, batch_limit=5)

    def raise_error(*_args, **_kwargs):
        raise RuntimeError("adapter offline")

    monkeypatch.setattr(embedding_backfill, "backfill_node_embeddings", raise_error)

    result = process_next_embedding_backfill_job(db, embedder="embedder")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    row = db.embedding_backfill_jobs.rows[0]
    assert row["status"] == "blocked"
    assert row["reason"] == "embedding_backfill_exception"
    assert row["error_count"] == 1
    assert row["last_result"]["activity_log"].startswith("Embedding Backfill Activity Log")


def test_list_embedding_backfill_jobs_filters_and_serializes() -> None:
    db = FakeDb()
    db.embedding_backfill_jobs.rows = [
        {"_id": ObjectId(), "status": "completed", "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"_id": ObjectId(), "status": "pending", "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ]

    jobs = list_embedding_backfill_jobs(db, status="pending")

    assert len(jobs) == 1
    assert jobs[0]["status"] == "pending"
    assert isinstance(jobs[0]["job_id"], str)


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCursor(list):
    def sort(self, field, direction=-1):
        super().sort(key=lambda row: row.get(field), reverse=direction < 0)
        return self

    def limit(self, limit):
        return FakeCursor(self[:limit])


class FakeCollection:
    def __init__(self):
        self.rows = []

    def insert_one(self, row):
        row = dict(row)
        row["_id"] = ObjectId()
        self.rows.append(row)
        return FakeInsertResult(row["_id"])

    def find(self, query=None):
        return FakeCursor([dict(row) for row in self.rows if matches(row, query or {})])

    def find_one_and_update(self, filter_query, update, sort=None, return_document=None):
        rows = [row for row in self.rows if matches(row, filter_query)]
        if not rows:
            return None
        if sort:
            for field, direction in reversed(sort):
                rows.sort(key=lambda row: row.get(field), reverse=direction < 0)
        apply_update(rows[0], update)
        return dict(rows[0])

    def update_one(self, filter_query, update):
        row = next((row for row in self.rows if matches(row, filter_query)), None)
        if row:
            apply_update(row, update)
        return None


class FakeDb:
    def __init__(self):
        self.embedding_backfill_jobs = FakeCollection()


def matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


def apply_update(row, update):
    row.update(update.get("$set", {}))
    for field, value in update.get("$inc", {}).items():
        row[field] = row.get(field, 0) + value
