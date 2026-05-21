from datetime import datetime, timezone

from bson import ObjectId

from mnemosyne.sessions.endorsements import (
    list_generated_output_nodes,
    update_node_endorsement,
)


def test_list_generated_output_nodes_filters_by_label_and_endorsement() -> None:
    node_id = ObjectId()
    db = FakeDb(
        [
            generated_node(node_id, "explicit_endorsed"),
            generated_node(ObjectId(), "unreviewed"),
            {
                "_id": ObjectId(),
                "document_id": ObjectId(),
                "tree_id": ObjectId(),
                "labels": ["source_chunk"],
                "endorsement_label": "explicit_endorsed",
                "title": "Source",
                "text": "Source",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )

    nodes = list_generated_output_nodes(db, endorsement_label="explicit_endorsed")

    assert [node["node_id"] for node in nodes] == [str(node_id)]
    assert nodes[0]["endorsement_label"] == "explicit_endorsed"


def test_update_node_endorsement_updates_label_provenance_and_review_history() -> None:
    node_id = ObjectId()
    db = FakeDb([generated_node(node_id, "unreviewed")])

    result = update_node_endorsement(
        db,
        str(node_id),
        "explicit_endorsed",
        reviewer="tester",
        note="Looks correct.",
    )

    row = db.nodes.rows[0]
    assert result["ok"] is True
    assert row["endorsement_label"] == "explicit_endorsed"
    assert row["provenance"]["endorsement_label"] == "explicit_endorsed"
    assert row["metadata"]["review"]["reviewer"] == "tester"
    assert row["metadata"]["review"]["note"] == "Looks correct."
    assert len(row["metadata"]["review_history"]) == 1
    assert result["node"]["review_history_count"] == 1
    assert result["node"]["review"]["reviewed_at"].endswith("+00:00")


def test_update_node_endorsement_rejects_invalid_node_id() -> None:
    result = update_node_endorsement(FakeDb([]), "bad", "rejected")

    assert result == {"ok": False, "reason": "invalid_node_id", "node_id": "bad"}


def test_update_node_endorsement_rejects_invalid_label() -> None:
    result = update_node_endorsement(FakeDb([]), str(ObjectId()), "trusted")

    assert result["ok"] is False
    assert result["reason"] == "invalid_endorsement_label"
    assert result["endorsement_label"] == "trusted"
    assert "explicit_endorsed" in result["allowed"]


def test_update_node_endorsement_reports_missing_node() -> None:
    node_id = ObjectId()

    result = update_node_endorsement(FakeDb([]), str(node_id), "rejected")

    assert result == {
        "ok": False,
        "reason": "node_not_found",
        "node_id": str(node_id),
    }


def test_update_node_endorsement_rejects_non_generated_output_node() -> None:
    node_id = ObjectId()
    db = FakeDb(
        [
            {
                "_id": node_id,
                "document_id": ObjectId(),
                "tree_id": ObjectId(),
                "title": "Source",
                "text": "Source text",
                "labels": ["source_chunk"],
                "endorsement_label": "unreviewed",
                "provenance": {"endorsement_label": "unreviewed"},
                "metadata": {},
                "created_at": datetime.now(timezone.utc),
            }
        ]
    )

    result = update_node_endorsement(db, str(node_id), "explicit_endorsed")

    assert result == {
        "ok": False,
        "reason": "not_generated_output",
        "node_id": str(node_id),
    }


def test_list_generated_output_nodes_rejects_invalid_filter() -> None:
    db = FakeDb([])

    try:
        list_generated_output_nodes(db, endorsement_label="trusted")
    except ValueError as error:
        assert "trusted" in str(error)
    else:
        raise AssertionError("Expected invalid endorsement filter to raise.")


def generated_node(node_id, endorsement_label):
    return {
        "_id": node_id,
        "document_id": ObjectId(),
        "tree_id": ObjectId(),
        "parent_id": None,
        "node_key": "answer",
        "parent_key": None,
        "title": "Answer",
        "text": "Generated answer text.",
        "labels": ["generated_output", "llm_answer", "source_chunk"],
        "endorsement_label": endorsement_label,
        "provenance": {"endorsement_label": endorsement_label},
        "metadata": {},
        "created_at": datetime.now(timezone.utc),
    }


class FakeCursor(list):
    def sort(self, field, direction):
        self[:] = sorted(self, key=lambda row: row.get(field), reverse=direction < 0)
        return self

    def limit(self, limit):
        return FakeCursor(self[:limit])


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        return FakeCursor([row for row in self.rows if matches(row, query)])

    def find_one(self, query):
        return next((row for row in self.rows if matches(row, query)), None)

    def update_one(self, filter_query, update):
        row = self.find_one(filter_query)
        if not row:
            return None
        apply_update(row, update)
        return None


class FakeDb:
    def __init__(self, nodes):
        self.nodes = FakeCollection(nodes)


def matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


def apply_update(row, update):
    for field, value in update.get("$set", {}).items():
        set_nested(row, field, value)
    for field, value in update.get("$push", {}).items():
        parts = field.split(".")
        target = row
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target.setdefault(parts[-1], []).append(value)


def set_nested(row, field, value):
    parts = field.split(".")
    target = row
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value
