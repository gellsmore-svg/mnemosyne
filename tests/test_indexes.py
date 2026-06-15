from pymongo.errors import CollectionInvalid

from tirzah.db.schema import REQUIRED_COLLECTIONS, collection_available, ensure_required_collections


def test_required_collection_inventory_includes_runtime_surfaces() -> None:
    assert REQUIRED_COLLECTIONS == tuple(sorted(REQUIRED_COLLECTIONS))
    assert "documents" in REQUIRED_COLLECTIONS
    assert "nodes" in REQUIRED_COLLECTIONS
    assert "graph_edges" in REQUIRED_COLLECTIONS
    assert "semantic_edge_candidates" in REQUIRED_COLLECTIONS
    assert "output_ingestion_queue" in REQUIRED_COLLECTIONS
    assert "retrieval_traces" in REQUIRED_COLLECTIONS


def test_ensure_required_collections_creates_missing_collections_only() -> None:
    db = FakeDb(existing={"documents", "nodes"})

    ensure_required_collections(db)

    assert "documents" not in db.created
    assert "nodes" not in db.created
    assert set(db.created) == set(REQUIRED_COLLECTIONS) - {"documents", "nodes"}


def test_ensure_required_collections_tolerates_creation_race() -> None:
    db = FakeDb(existing=set(), fail_on={"queue"})

    ensure_required_collections(db)

    assert "queue" in db.create_attempts
    assert "documents" in db.created


def test_collection_available_uses_collection_inventory_when_available() -> None:
    db = FakeDb(existing={"documents"})

    assert collection_available(db, "documents") is True
    assert collection_available(db, "graph_edges") is False


def test_collection_available_falls_back_to_attribute_guard_without_inventory() -> None:
    db = AttributeOnlyDb()
    db.documents = object()

    assert collection_available(db, "documents") is True
    assert collection_available(db, "graph_edges") is False


class FakeDb:
    def __init__(self, *, existing: set[str], fail_on: set[str] | None = None) -> None:
        self.existing = set(existing)
        self.fail_on = fail_on or set()
        self.create_attempts = []
        self.created = []

    def list_collection_names(self) -> list[str]:
        return sorted(self.existing)

    def create_collection(self, name: str) -> None:
        self.create_attempts.append(name)
        if name in self.fail_on:
            raise CollectionInvalid(name)
        self.existing.add(name)
        self.created.append(name)


class AttributeOnlyDb:
    pass
