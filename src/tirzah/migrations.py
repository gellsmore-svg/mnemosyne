"""Consolidated, ordered schema migrations (`tirzah migrate`).

Tirzah already stamps `schema_version` on its records (documents / trees / nodes,
sessions, …), but migrations of legacy data lived in scattered one-shot CLI
commands (`backfill-schema-metadata`, `backfill-source-metadata`) with no record of
what had run. This module gathers the data migrations into a single **ordered,
idempotent** registry with a ledger, so a fresh or upgraded store can be brought
current in one command — the hook Noa's `upgrade.sh` calls.

Mirrors Mahalath's `migrate` framework deliberately, so the family shares one
mental model. Applied migrations are recorded in the `schema_migrations`
collection (`{_id: <number>, name, applied_at, result}`); `migrate()` runs only the
pending ones, in order. The underlying backfills are themselves idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pymongo.database import Database

LEDGER = "schema_migrations"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    description: str
    run: Callable[[Database], dict[str, Any]]


def _backfill_schema_metadata(db: Database) -> dict[str, Any]:
    from tirzah.db.repositories import backfill_schema_metadata

    return backfill_schema_metadata(db)


def backfill_source_metadata(db: Database, *, archive_dir: Path | None = None) -> dict[str, Any]:
    """Backfill source checksums/archive paths for legacy documents.

    Idempotent: documents that already carry ``source.checksum_sha256`` are left
    alone, and missing source files are reported as skipped rather than failing
    the whole migration.
    """
    from tirzah.ingestion.files import archive_source, sha256_file

    if archive_dir is None:
        from tirzah.config import load_config

        archive_dir = load_config().paths.archive
    updated = []
    skipped = []
    for document in db.documents.find({"source.checksum_sha256": {"$exists": False}}):
        source = document.get("source") or {}
        source_path = Path(str(source.get("path") or ""))
        if not source.get("path") or not source_path.is_file():
            skipped.append(
                {
                    "document_id": str(document.get("_id")),
                    "reason": "source_missing",
                    "path": str(source_path),
                }
            )
            continue
        checksum = sha256_file(source_path)
        archived_path = archive_source(source_path, archive_dir, checksum)
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
    return {"updated": updated, "skipped": skipped}


# Ordered registry. Append new migrations with the next number; never renumber.
MIGRATIONS: list[Migration] = [
    Migration(1, "backfill_schema_metadata",
              "Stamp schema_version (+ derived metadata) on documents/trees/nodes "
              "that predate the field.",
              _backfill_schema_metadata),
    Migration(2, "backfill_source_metadata",
              "Stamp source checksum/archive metadata on legacy documents.",
              backfill_source_metadata),
]


def applied_numbers(db: Database) -> set[int]:
    return {doc["_id"] for doc in db[LEDGER].find({}, {"_id": 1})}


def pending(db: Database) -> list[Migration]:
    done = applied_numbers(db)
    return [m for m in MIGRATIONS if m.number not in done]


def current_version(db: Database) -> int:
    done = applied_numbers(db)
    return max(done) if done else 0


def _record(db: Database, migration: Migration, result: dict[str, Any]) -> None:
    db[LEDGER].insert_one({
        "_id": migration.number,
        "name": migration.name,
        "applied_at": _utcnow(),
        "result": result,
    })


def status(db: Database) -> dict[str, Any]:
    done = applied_numbers(db)
    return {
        "current_version": max(done) if done else 0,
        "target_version": MIGRATIONS[-1].number if MIGRATIONS else 0,
        "applied": sorted(done),
        "pending": [{"number": m.number, "name": m.name, "description": m.description}
                    for m in MIGRATIONS if m.number not in done],
    }


def migrate(db: Database, *, dry_run: bool = False) -> dict[str, Any]:
    """Run pending migrations in order (idempotent). Returns a per-migration report."""
    ran: list[dict[str, Any]] = []
    for migration in pending(db):
        if dry_run:
            ran.append({"number": migration.number, "name": migration.name, "status": "would-run"})
            continue
        result = migration.run(db)
        _record(db, migration, result)
        ran.append({"number": migration.number, "name": migration.name,
                    "status": "applied", "result": result})
    return {
        "ok": True,
        "dry_run": dry_run,
        "ran": ran,
        "current_version": current_version(db) if not dry_run else status(db)["current_version"],
        "target_version": MIGRATIONS[-1].number if MIGRATIONS else 0,
    }
