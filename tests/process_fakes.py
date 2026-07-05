"""Shared in-memory Mongo fakes for the process-management tests.

A dict-backed db whose collections spring into existence on attribute access,
supporting the query subset the process modules use (equality match, projection
{"_id": 0}, sort, limit, $set/$push updates).
"""

from __future__ import annotations

from typing import Any


def matches(row: dict, query: dict) -> bool:
    return all(row.get(key) == value for key, value in query.items())


class FakeCursor(list):
    def sort(self, field, direction=1):
        super().sort(key=lambda row: row.get(field), reverse=direction < 0)
        return self

    def limit(self, value):
        return FakeCursor(self[:value])


class FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def find(self, query=None, projection=None):
        query = query or {}
        out = []
        for row in self.rows:
            if not matches(row, query):
                continue
            if projection and projection.get("_id") == 0:
                out.append({k: v for k, v in row.items() if k != "_id"})
            else:
                out.append(dict(row))
        return FakeCursor(out)

    def find_one(self, query=None, projection=None):
        query = query or {}
        row = next((item for item in self.rows if matches(item, query)), None)
        if row is None:
            return None
        if projection and projection.get("_id") == 0:
            return {k: v for k, v in row.items() if k != "_id"}
        return dict(row)

    def insert_one(self, row):
        self.rows.append(dict(row))
        return None

    def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if matches(item, query)), None)
        if row is None:
            if not upsert:
                return None
            row = {**query, **update.get("$setOnInsert", {})}
            self.rows.append(row)
        row.update(update.get("$set", {}))
        for key, value in update.get("$push", {}).items():
            row.setdefault(key, []).append(value)
        return None


class FakeDb:
    """Collections created on first access (db.process_templates, etc.)."""

    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getattr__(self, name: str) -> FakeCollection:
        # __getattr__ only fires for missing attributes, so _collections
        # (set in __init__) is safe from recursion.
        collections = self.__dict__.setdefault("_collections", {})
        return collections.setdefault(name, FakeCollection())

    def __getitem__(self, name: str) -> FakeCollection:
        return getattr(self, name)
