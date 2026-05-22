from datetime import datetime, timezone

from bson import ObjectId

from mnemosyne.db.repositories import (
    backfill_structural_graph_edges,
    bounded_graph_group_limit,
    commit_ingestion,
    graph_edge_status,
    rebuild_document,
)
from mnemosyne.models.ingestion import IngestedNode, IngestionResult, SourceRef


def test_commit_ingestion_rolls_back_partial_insert_on_node_failure() -> None:
    db = FakeDb(fail_nodes=True)
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[
            IngestedNode(node_key="root", title="Root", text="Root text"),
        ],
        created_at=datetime.now(timezone.utc),
    )

    try:
        commit_ingestion(db, result)
    except RuntimeError as error:
        assert "node insert failed" in str(error)
    else:
        raise AssertionError("Expected commit_ingestion to propagate node insert failure.")

    assert db.documents.rows == []
    assert db.trees.rows == []
    assert db.nodes.rows == []


def test_rebuild_document_restores_previous_records_on_node_failure() -> None:
    document_id = ObjectId()
    tree_id = ObjectId()
    node_id = ObjectId()
    db = FakeDb(fail_nodes=True)
    db.documents.rows.append(
        {
            "_id": document_id,
            "title": "Old",
            "summary": "Old summary",
            "source": {"path": "old.md", "kind": "markdown"},
        }
    )
    db.trees.rows.append({"_id": tree_id, "document_id": document_id, "label": "source"})
    db.nodes.rows.append(
        {
            "_id": node_id,
            "document_id": document_id,
            "tree_id": tree_id,
            "node_key": "root",
            "title": "Old root",
        }
    )
    result = IngestionResult(
        source=SourceRef(path="new.md", kind="markdown", checksum_sha256="new"),
        title="New",
        summary="New summary",
        nodes=[
            IngestedNode(node_key="root", title="New root", text="New text"),
        ],
        created_at=datetime.now(timezone.utc),
    )

    try:
        rebuild_document(db, str(document_id), result)
    except RuntimeError as error:
        assert "node insert failed" in str(error)
    else:
        raise AssertionError("Expected rebuild_document to propagate node insert failure.")

    assert db.documents.rows == [
        {
            "_id": document_id,
            "title": "Old",
            "summary": "Old summary",
            "source": {"path": "old.md", "kind": "markdown"},
        }
    ]
    assert db.trees.rows == [{"_id": tree_id, "document_id": document_id, "label": "source"}]
    assert db.nodes.rows == [
        {
            "_id": node_id,
            "document_id": document_id,
            "tree_id": tree_id,
            "node_key": "root",
            "title": "Old root",
        }
    ]


def test_commit_ingestion_persists_relation_edges() -> None:
    db = FakeDb()
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[
            IngestedNode(node_key="root", title="Root", text="Root text"),
            IngestedNode(
                node_key="child",
                parent_key="root",
                title="Child",
                text="Child text",
                relations=[
                    {
                        "type": "supports",
                        "target_node_key": "root",
                        "weight": "0.75",
                        "confidence": 0.8,
                    },
                    {
                        "type": "supports",
                        "target_node_key": "root",
                    },
                    {
                        "type": "mentions",
                        "target_node_key": "missing",
                    },
                ],
            ),
        ],
        created_at=datetime.now(timezone.utc),
    )

    inserted = commit_ingestion(db, result)

    assert inserted["edge_count"] == 1
    assert inserted["skipped_edge_count"] == 2
    assert len(db.graph_edges.rows) == 1
    edge = db.graph_edges.rows[0]
    assert edge["relation_type"] == "supports"
    assert edge["source_node_key"] == "child"
    assert edge["target_node_key"] == "root"
    assert edge["weight"] == 0.75
    assert edge["confidence"] == 0.8
    assert edge["provenance"]["source"] == "ingestion_node_relation"
    assert edge["source_node_id"] != edge["target_node_id"]


def test_backfill_structural_graph_edges_creates_parent_child_edges() -> None:
    db = FakeDb()
    document_id = ObjectId()
    tree_id = ObjectId()
    parent_id = ObjectId()
    child_id = ObjectId()
    missing_parent_child_id = ObjectId()
    db.nodes.rows.extend(
        [
            {
                "_id": parent_id,
                "document_id": document_id,
                "tree_id": tree_id,
                "node_key": "root",
                "title": "Root",
                "parent_id": None,
            },
            {
                "_id": child_id,
                "document_id": document_id,
                "tree_id": tree_id,
                "node_key": "child",
                "title": "Child",
                "parent_id": parent_id,
            },
            {
                "_id": missing_parent_child_id,
                "document_id": document_id,
                "tree_id": tree_id,
                "node_key": "orphan",
                "title": "Orphan",
                "parent_id": ObjectId(),
            },
        ]
    )

    result = backfill_structural_graph_edges(db)

    assert result == {
        "scanned_node_count": 2,
        "edge_count": 1,
        "skipped_existing_count": 0,
        "skipped_missing_parent_count": 1,
    }
    assert len(db.graph_edges.rows) == 1
    edge = db.graph_edges.rows[0]
    assert edge["source_node_id"] == parent_id
    assert edge["target_node_id"] == child_id
    assert edge["source_node_key"] == "root"
    assert edge["target_node_key"] == "child"
    assert edge["relation_type"] == "contains"
    assert edge["weight"] == 1.0
    assert edge["confidence"] == 1.0
    assert edge["provenance"]["source"] == "node_parent_link"

    second = backfill_structural_graph_edges(db)

    assert second["edge_count"] == 0
    assert second["skipped_existing_count"] == 1


def test_graph_edge_status_counts_relations_and_provenance_sources() -> None:
    db = FakeDb()
    db.graph_edges.rows.extend(
        [
            {
                "relation_type": "contains",
                "provenance": {"source": "node_parent_link"},
            },
            {
                "relation_type": "contains",
                "provenance": {"source": "node_parent_link"},
            },
            {
                "relation_type": "supports",
                "provenance": {"source": "ingestion_node_relation"},
            },
        ]
    )

    assert graph_edge_status(db) == {
        "edge_count": 3,
        "relation_types": [
            {"value": "contains", "count": 2},
            {"value": "supports", "count": 1},
        ],
        "provenance_sources": [
            {"value": "node_parent_link", "count": 2},
            {"value": "ingestion_node_relation", "count": 1},
        ],
    }


def test_graph_edge_status_handles_missing_graph_edges_collection() -> None:
    db = object()

    assert graph_edge_status(db) == {
        "edge_count": 0,
        "relation_types": [],
        "provenance_sources": [],
    }


def test_graph_edge_status_reports_null_buckets() -> None:
    db = FakeDb()
    db.graph_edges.rows.append({})

    assert graph_edge_status(db) == {
        "edge_count": 1,
        "relation_types": [{"value": None, "count": 1}],
        "provenance_sources": [{"value": None, "count": 1}],
    }


def test_graph_edge_status_applies_limit_to_group_buckets() -> None:
    db = FakeDb()
    db.graph_edges.rows.extend(
        [
            {"relation_type": "alpha", "provenance": {"source": "a"}},
            {"relation_type": "beta", "provenance": {"source": "b"}},
            {"relation_type": "gamma", "provenance": {"source": "c"}},
        ]
    )

    result = graph_edge_status(db, limit=2)

    assert result["relation_types"] == [
        {"value": "alpha", "count": 1},
        {"value": "beta", "count": 1},
    ]
    assert result["provenance_sources"] == [
        {"value": "a", "count": 1},
        {"value": "b", "count": 1},
    ]


def test_bounded_graph_group_limit_clamps_explicit_limits() -> None:
    assert bounded_graph_group_limit(0) == 1
    assert bounded_graph_group_limit(-5) == 1
    assert bounded_graph_group_limit(999) == 50
    assert bounded_graph_group_limit("bad") == 10


def test_commit_ingestion_rolls_back_edges_on_insert_failure() -> None:
    db = FakeDb(fail_edges=True)
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[
            IngestedNode(node_key="root", title="Root", text="Root text"),
            IngestedNode(
                node_key="child",
                title="Child",
                text="Child text",
                relations=[{"type": "supports", "target_node_key": "root"}],
            ),
        ],
        created_at=datetime.now(timezone.utc),
    )

    try:
        commit_ingestion(db, result)
    except RuntimeError as error:
        assert "edge insert failed" in str(error)
    else:
        raise AssertionError("Expected commit_ingestion to propagate edge insert failure.")

    assert db.documents.rows == []
    assert db.trees.rows == []
    assert db.nodes.rows == []
    assert db.graph_edges.rows == []


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, fail_insert=False, fail_insert_many=False):
        self.rows = []
        self.fail_insert = fail_insert
        self.fail_insert_many = fail_insert_many

    def find_one(self, query):
        row = next((row for row in self.rows if matches(row, query)), None)
        return dict(row) if row else None

    def find(self, query):
        return [dict(row) for row in self.rows if matches(row, query)]

    def count_documents(self, query):
        return len([row for row in self.rows if matches(row, query)])

    def aggregate(self, pipeline):
        group_field = pipeline[0]["$group"]["_id"].removeprefix("$")
        sort_spec = pipeline[1].get("$sort", {})
        counts = {}
        for row in self.rows:
            value = nested_get(row, group_field)
            counts[value] = counts.get(value, 0) + 1
        rows = [{"_id": key, "count": value} for key, value in counts.items()]
        for field, direction in reversed(list(sort_spec.items())):
            rows.sort(
                key=lambda item, sort_field=field: mongo_sort_value(item.get(sort_field)),
                reverse=direction < 0,
            )
        limit = pipeline[-1].get("$limit", len(rows))
        return rows[:limit]

    def insert_one(self, row):
        if self.fail_insert:
            raise RuntimeError("node insert failed")
        row = dict(row)
        row["_id"] = ObjectId()
        self.rows.append(row)
        return FakeInsertResult(row["_id"])

    def insert_many(self, rows):
        if self.fail_insert_many:
            raise RuntimeError("edge insert failed")
        for row in rows:
            self.rows.append(dict(row))
        return None

    def replace_one(self, query, replacement):
        for index, row in enumerate(self.rows):
            if matches(row, query):
                self.rows[index] = dict(replacement)
                break
        return None

    def update_one(self, query, update):
        row = next((item for item in self.rows if matches(item, query)), None)
        if row:
            row.update(update.get("$set", {}))
        return None

    def delete_many(self, query):
        self.rows = [row for row in self.rows if not matches(row, query)]
        return None

    def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if matches(row, query):
                del self.rows[index]
                break
        return None


class FakeDb:
    def __init__(self, fail_nodes=False, fail_edges=False):
        self.documents = FakeCollection()
        self.trees = FakeCollection()
        self.nodes = FakeCollection(fail_insert=fail_nodes)
        self.graph_edges = FakeCollection(fail_insert_many=fail_edges)


def matches(row, query):
    for key, expected in query.items():
        if isinstance(expected, dict):
            if "$exists" in expected and (key in row) is not expected["$exists"]:
                return False
            if "$ne" in expected and row.get(key) == expected["$ne"]:
                return False
            continue
        if row.get(key) != expected:
            return False
    return True


def nested_get(row, dotted_key):
    value = row
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def mongo_sort_value(value):
    if value is None:
        return (0, "")
    return (1, value)
