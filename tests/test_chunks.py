from __future__ import annotations

import json

import pytest
from bson import ObjectId

from tirzah.sessions.chunks import (
    heuristic_chunks,
    list_chunks,
    parse_chunks,
    pending_chunk_exchanges,
    store_chunks,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self._rows[:n]

    def __iter__(self):
        return iter(self._rows)


class FakeColl:
    def __init__(self):
        self.rows: list[dict] = []

    def insert_many(self, rows):
        self.rows.extend(dict(r) for r in rows)

    def find(self, query=None, projection=None):
        query = query or {}
        out = []
        for row in self.rows:
            ok = True
            for key, val in query.items():
                if isinstance(val, dict) and "$ne" in val:
                    if row.get(key) == val["$ne"]:
                        ok = False
                elif row.get(key) != val:
                    ok = False
            if ok:
                out.append({k: v for k, v in row.items() if k != "_id"} if projection else row)
        return FakeCursor(out)

    def update_one(self, query, update):
        for row in self.rows:
            if row.get("_id") == query.get("_id"):
                row.update(update.get("$set", {}))


class FakeDb:
    def __init__(self):
        self._collections: dict[str, FakeColl] = {}
        self.exchanges = FakeColl()

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeColl())


def test_parse_chunks_validates_and_normalizes() -> None:
    chunks = parse_chunks(json.dumps({"chunks": [
        {"kind": "topic", "text": "Ollama"},
        {"kind": "BOGUS", "text": "fallback to topic"},
        {"kind": "intent", "text": ""},  # dropped (empty)
    ]}))
    assert chunks[0] == {"kind": "topic", "text": "Ollama"}
    assert chunks[1]["kind"] == "topic"  # unknown kind coerced
    assert len(chunks) == 2
    with pytest.raises(ValueError):
        parse_chunks("not json")
    with pytest.raises(ValueError):
        parse_chunks(json.dumps({"chunks": []}))


def test_heuristic_chunks() -> None:
    assert heuristic_chunks("compare X and Y", "")[0] == {"kind": "topic", "text": "compare X and Y"}
    assert heuristic_chunks("", "") == []


def test_store_and_query_chunks() -> None:
    db = FakeDb()
    oid = ObjectId()
    db.exchanges.rows.append({"_id": oid, "session_id": "s1", "query": "q", "answer": {"answer": "a"}})

    n = store_chunks(db, exchange_id=str(oid), session_id="s1", chunks=[{"kind": "topic", "text": "X"}])
    assert n == 1
    assert db.exchanges.rows[0]["chunked"] is True  # exchange marked chunked
    assert list_chunks(db, session_id="s1")[0]["text"] == "X"

    # the chunked exchange is no longer pending
    assert pending_chunk_exchanges(db) == []
    # an un-chunked exchange IS pending, and carries session_id
    db.exchanges.rows.append({"_id": ObjectId(), "session_id": "s2", "query": "q2", "answer": {"answer": "a2"}})
    pending = pending_chunk_exchanges(db)
    assert len(pending) == 1 and pending[0]["session_id"] == "s2"
