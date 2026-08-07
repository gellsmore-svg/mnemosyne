"""Deborah open-questions Mongo store (Tirzah estate)."""

from __future__ import annotations

from tirzah.open_questions import (
    OPEN_QUESTIONS_COLLECTION,
    list_open_questions,
    record_open_question,
    try_get_database,
)


class _FakeCol:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def create_index(self, *a, **k):
        return "ok"

    def insert_one(self, doc):
        self.rows.append(dict(doc))

    def find(self, query=None, projection=None):
        query = query or {}
        rows = [
            {k: v for k, v in r.items() if k != "_id"}
            for r in self.rows
            if all(r.get(k) == v for k, v in query.items())
        ]

        class _C:
            def sort(self, *_a, **_k):
                return self

            def limit(self, n):
                return rows[:n]

            def __iter__(self):
                return iter(rows)

        return _C()


class _FakeDb:
    def __init__(self) -> None:
        self.col = _FakeCol()

    def __getitem__(self, name):
        assert name == OPEN_QUESTIONS_COLLECTION
        return self.col


def test_record_and_list_open_questions_fake_db() -> None:
    db = _FakeDb()
    doc = {
        "open_question_id": "oq_test1",
        "question": "Is substrate coherence well-supported?",
        "reason": "empty evidence",
        "plan_id": "plan_x",
        "created_at": "2026-08-07T12:00:00+00:00",
    }
    assert record_open_question(db, doc) is not None
    listed = list_open_questions(db, plan_id="plan_x")
    assert len(listed) == 1
    assert listed[0]["open_question_id"] == "oq_test1"


def test_try_get_database_smoke() -> None:
    """Live Mongo if available; None otherwise — never raises."""
    db = try_get_database()
    # Either reachable or not; both are valid outcomes for the harness.
    assert db is None or hasattr(db, "__getitem__")
