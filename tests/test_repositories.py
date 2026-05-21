from datetime import datetime, timezone

from bson import ObjectId

from mnemosyne.db.repositories import commit_ingestion
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


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, fail_insert=False):
        self.rows = []
        self.fail_insert = fail_insert

    def find_one(self, query):
        return next((row for row in self.rows if matches(row, query)), None)

    def insert_one(self, row):
        if self.fail_insert:
            raise RuntimeError("node insert failed")
        row = dict(row)
        row["_id"] = ObjectId()
        self.rows.append(row)
        return FakeInsertResult(row["_id"])

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
    def __init__(self, fail_nodes=False):
        self.documents = FakeCollection()
        self.trees = FakeCollection()
        self.nodes = FakeCollection(fail_insert=fail_nodes)


def matches(row, query):
    for key, expected in query.items():
        if row.get(key) != expected:
            return False
    return True
