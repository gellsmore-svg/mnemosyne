from datetime import datetime, timezone

from tirzah.sessions.registry import (
    clean_session_id,
    create_session,
    list_sessions,
    touch_session,
)


class FakeInsertResult:
    inserted_id = "unused"


class FakeCollection:
    def __init__(self) -> None:
        self.rows = {}

    def update_one(self, filter_query, update, upsert=False):
        session_id = filter_query["session_id"]
        row = self.rows.get(session_id)
        if row is None:
            row = {}
            row.update(update.get("$setOnInsert", {}))
            self.rows[session_id] = row
        row.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value

    def find_one(self, filter_query, sort=None):
        if "session_id" in filter_query:
            return self.rows.get(filter_query["session_id"])
        if not self.rows:
            return None
        return next(iter(self.rows.values()))

    def find(self, filter_query):
        return FakeCursor(list(self.rows.values()))

    def distinct(self, field):
        return []

    def count_documents(self, filter_query):
        return 0


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, field, direction):
        reverse = direction < 0
        self.rows.sort(key=lambda row: row.get(field) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse)
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeDb:
    def __init__(self) -> None:
        self.sessions = FakeCollection()
        self.exchanges = FakeCollection()


def test_clean_session_id_removes_unsafe_characters() -> None:
    assert clean_session_id(" My Session!? ") == "My-Session"


def test_create_and_touch_session() -> None:
    db = FakeDb()

    created = create_session(db, title="Design Notes", session_id="design")
    touch_session(db, "design")
    listed = list_sessions(db)

    assert created["session_id"] == "design"
    assert listed[0]["session_id"] == "design"
    assert listed[0]["exchange_count"] == 1
    assert listed[0]["last_exchange_at"]
