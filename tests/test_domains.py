from datetime import datetime, timezone

from tirzah.domains.registry import (
    bounded_domain_limit,
    clean_domain_id,
    conversation_domain_id_for_session,
    ensure_conversation_domain,
    ensure_project_domain,
    get_conversation_domain,
    get_project_domain,
    list_conversation_domains,
    list_project_domains,
)


def test_clean_domain_id_keeps_safe_identifier_shape() -> None:
    assert clean_domain_id(" Project / One ") == "Project-One"
    assert clean_domain_id(" ") == "tirzah"
    assert conversation_domain_id_for_session("Chat Thread!") == "Chat-Thread"


def test_project_domain_upsert_lists_and_serializes_dates() -> None:
    db = FakeDb()

    domain = ensure_project_domain(
        db,
        domain_id="tirzah",
        title="Tirzah",
        description="Main product domain",
        created_by="tester",
    )

    assert domain["domain_id"] == "tirzah"
    assert domain["title"] == "Tirzah"
    assert domain["description"] == "Main product domain"
    assert "created_at" in domain
    assert get_project_domain(db, "tirzah")["created_by"] == "tester"
    assert [row["domain_id"] for row in list_project_domains(db)] == ["tirzah"]


def test_conversation_domain_creates_parent_project_domain() -> None:
    db = FakeDb()

    conversation = ensure_conversation_domain(
        db,
        domain_id="session-1",
        project_domain_id="tirzah",
        title="Session 1",
        session_id="session-1",
    )

    assert conversation["domain_id"] == "session-1"
    assert conversation["project_domain_id"] == "tirzah"
    assert get_project_domain(db, "tirzah")["domain_id"] == "tirzah"
    assert get_conversation_domain(db, "session-1")["session_id"] == "session-1"
    assert [row["domain_id"] for row in list_conversation_domains(db, project_domain_id="tirzah")] == ["session-1"]
    assert [row["domain_id"] for row in list_conversation_domains(db, session_id="session-1")] == ["session-1"]


def test_conversation_domain_does_not_overwrite_project_domain_metadata() -> None:
    db = FakeDb()
    ensure_project_domain(
        db,
        domain_id="tirzah",
        title="Tirzah Product",
        description="Existing domain description",
        created_by="tester",
    )

    ensure_conversation_domain(db, domain_id="thread-1", project_domain_id="tirzah")

    project = get_project_domain(db, "tirzah")
    assert project["title"] == "Tirzah Product"
    assert project["description"] == "Existing domain description"
    assert project["created_by"] == "tester"


def test_bounded_domain_limit_clamps_values() -> None:
    assert bounded_domain_limit(0) == 1
    assert bounded_domain_limit(500) == 100
    assert bounded_domain_limit("bad") == 20


class FakeCursor(list):
    def sort(self, field, direction=1):
        self[:] = sorted(
            self,
            key=lambda row: row.get(field) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=direction < 0,
        )
        return self

    def limit(self, limit):
        return FakeCursor(self[:limit])


class FakeCollection:
    def __init__(self):
        self.rows = []

    def find(self, query=None, projection=None):
        return FakeCursor([project(row, projection) for row in self.rows if matches(row, query or {})])

    def find_one(self, query=None, projection=None):
        rows = self.find(query or {}, projection)
        return rows[0] if rows else None

    def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if matches(item, query)), None)
        if row is None:
            if not upsert:
                return None
            row = dict(query)
            row.update(update.get("$setOnInsert", {}))
            self.rows.append(row)
        row.update(update.get("$set", {}))
        return None


class FakeDb:
    def __init__(self):
        self.project_domains = FakeCollection()
        self.conversation_domains = FakeCollection()


def matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


def project(row, projection):
    if not projection:
        return dict(row)
    if projection == {"_id": 0}:
        return {key: value for key, value in row.items() if key != "_id"}
    projected = {key: row[key] for key in projection if key in row}
    if "_id" in row and projection.get("_id", 1):
        projected["_id"] = row["_id"]
    return projected
