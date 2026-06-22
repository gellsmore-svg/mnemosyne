"""Tests for the consolidated `migrate` framework (tirzah.migrations)."""

from __future__ import annotations

import pytest

from tirzah import migrations
from tirzah.migrations import LEDGER, Migration


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def find(self, query=None, projection=None):
        return list(self.docs)

    def insert_one(self, doc):
        if any(d["_id"] == doc["_id"] for d in self.docs):
            raise RuntimeError("duplicate key")
        self.docs.append(doc)

    def count_documents(self, query):
        return len(self.docs)


class _FakeDb:
    def __init__(self) -> None:
        self.cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, _FakeCollection())


@pytest.fixture
def db():
    return _FakeDb()


@pytest.fixture
def two_migrations(monkeypatch):
    ran: list[int] = []
    migs = [
        Migration(1, "first", "d1", lambda _db: ran.append(1) or {"updated": 1}),
        Migration(2, "second", "d2", lambda _db: ran.append(2) or {"updated": 2}),
    ]
    monkeypatch.setattr(migrations, "MIGRATIONS", migs)
    return ran


def test_status_on_fresh_db_lists_all_pending(db, two_migrations):
    st = migrations.status(db)
    assert st["current_version"] == 0 and st["target_version"] == 2
    assert [m["number"] for m in st["pending"]] == [1, 2]


def test_migrate_runs_all_pending_records_them(db, two_migrations):
    report = migrations.migrate(db)
    assert [r["number"] for r in report["ran"]] == [1, 2]
    assert all(r["status"] == "applied" for r in report["ran"])
    assert report["current_version"] == 2
    assert two_migrations == [1, 2]                       # both run, in order
    assert db[LEDGER].count_documents({}) == 2


def test_migrate_is_idempotent(db, two_migrations):
    migrations.migrate(db)
    again = migrations.migrate(db)
    assert again["ran"] == []
    assert two_migrations == [1, 2]                       # not re-run


def test_dry_run_changes_nothing(db, two_migrations):
    report = migrations.migrate(db, dry_run=True)
    assert report["dry_run"] is True
    assert all(r["status"] == "would-run" for r in report["ran"])
    assert db[LEDGER].count_documents({}) == 0
    assert two_migrations == []                           # nothing actually ran


def test_partial_ledger_only_runs_remaining(db, two_migrations):
    db[LEDGER].insert_one({"_id": 1, "name": "first"})
    assert [m.number for m in migrations.pending(db)] == [2]
    report = migrations.migrate(db)
    assert [r["number"] for r in report["ran"]] == [2]
    assert two_migrations == [2]


def test_real_registry_has_schema_metadata_migration():
    # The shipped registry wires the real backfill as migration 1.
    assert migrations.MIGRATIONS[0].number == 1
    assert migrations.MIGRATIONS[0].name == "backfill_schema_metadata"
