from datetime import datetime, timezone

from bson import ObjectId

from mnemosyne.sessions.active_documents import (
    list_active_documents,
    record_active_documents,
    valid_object_ids,
)


class FakeUpdateResult:
    pass


class FakeCursor(list):
    def sort(self, field, direction):
        reverse = direction < 0
        sorted_rows = sorted(
            self,
            key=lambda row: row.get(field) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=reverse,
        )
        self[:] = sorted_rows
        return self

    def limit(self, limit):
        return FakeCursor(self[:limit])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query, projection=None):
        rows = [row for row in self.rows if matches(row, query)]
        return FakeCursor([project(row, projection) for row in rows])

    def update_one(self, filter_query, update, upsert=False):
        row = next((item for item in self.rows if matches(item, filter_query)), None)
        if row is None:
            row = {}
            row.update(update.get("$setOnInsert", {}))
            row.update(filter_query)
            self.rows.append(row)
        row.update(update.get("$set", {}))
        for field, value in update.get("$inc", {}).items():
            row[field] = row.get(field, 0) + value
        for field, value in update.get("$addToSet", {}).items():
            row.setdefault(field, [])
            for item in value.get("$each", []):
                if item not in row[field]:
                    row[field].append(item)
        return FakeUpdateResult()


class FakeDb:
    def __init__(self, document_id, node_id):
        other_node_id = ObjectId()
        self.other_node_id = other_node_id
        self.documents = FakeCollection(
            [
                {
                    "_id": document_id,
                    "title": "Technical Design",
                    "source": {"path": "design.md"},
                }
            ]
        )
        self.nodes = FakeCollection(
            [
                {
                    "_id": node_id,
                    "document_id": document_id,
                    "title": "System Name",
                    "labels": ["source_section", "mnemosyne"],
                    "provenance": {},
                },
                {
                    "_id": other_node_id,
                    "document_id": document_id,
                    "title": "Retrieval Notes",
                    "labels": ["retrieval_note"],
                    "provenance": {},
                }
            ]
        )
        self.active_documents = FakeCollection()


def test_valid_object_ids_deduplicates_and_skips_invalid_values() -> None:
    object_id = ObjectId()

    assert valid_object_ids([str(object_id), "bad", str(object_id)]) == [object_id]


def test_record_active_documents_upserts_session_document_registry() -> None:
    document_id = ObjectId()
    node_id = ObjectId()
    db = FakeDb(document_id, node_id)

    active_ids = record_active_documents(db, "session-1", [str(node_id)])
    record_active_documents(db, "session-1", [str(node_id)])

    assert active_ids == [str(document_id)]
    row = db.active_documents.rows[0]
    assert row["session_id"] == "session-1"
    assert row["document_id"] == str(document_id)
    assert row["title"] == "Technical Design"
    assert row["source"] == {"path": "design.md"}
    assert row["labels"] == ["mnemosyne", "source_section"]
    assert row["node_ids"] == [str(node_id)]
    assert row["reference_count"] == 2


def test_record_active_documents_accumulates_labels_across_references() -> None:
    document_id = ObjectId()
    node_id = ObjectId()
    db = FakeDb(document_id, node_id)

    record_active_documents(db, "session-1", [str(node_id)])
    record_active_documents(db, "session-1", [str(db.other_node_id)])

    row = db.active_documents.rows[0]
    assert row["node_ids"] == [str(node_id), str(db.other_node_id)]
    assert row["labels"] == ["mnemosyne", "source_section", "retrieval_note"]
    assert row["reference_count"] == 2


def test_list_active_documents_serializes_recent_documents() -> None:
    document_id = ObjectId()
    node_id = ObjectId()
    db = FakeDb(document_id, node_id)

    record_active_documents(db, "session-1", [str(node_id)])

    assert list_active_documents(db, "session-1")[0]["document_id"] == str(document_id)


def matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def project(row, projection):
    if not projection:
        return dict(row)
    projected = {key: row[key] for key in projection if key in row}
    if "_id" in row and projection.get("_id", 1):
        projected["_id"] = row["_id"]
    return projected
