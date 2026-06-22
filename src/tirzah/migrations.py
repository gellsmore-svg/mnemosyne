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


# Ordered registry. Append new migrations with the next number; never renumber.
MIGRATIONS: list[Migration] = [
    Migration(1, "backfill_schema_metadata",
              "Stamp schema_version (+ derived metadata) on documents/trees/nodes "
              "that predate the field.",
              _backfill_schema_metadata),
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
