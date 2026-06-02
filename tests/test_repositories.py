from datetime import datetime, timezone

from bson import ObjectId

from mnemosyne.db.repositories import (
    backfill_node_embeddings,
    backfill_structural_graph_edges,
    bounded_graph_group_limit,
    commit_ingestion,
    create_reviewed_semantic_edge,
    document_tree,
    enqueue_semantic_edge_candidates,
    enqueue_vector_semantic_edge_candidates,
    graph_edge_status,
    list_semantic_edge_candidates,
    rebuild_document,
    review_semantic_edge_candidate,
    resolved_ingestion_epoch,
)
from mnemosyne.models.ingestion import IngestedNode, IngestionResult, SourceRef


class FakeEmbedder:
    name = "fake_embedding"
    model = "fake-model"
    dimensions = 2

    def embed(self, text):
        return {
            "adapter": self.name,
            "model": self.model,
            "dimensions": self.dimensions,
            "vector": [1.0, 0.0],
            "source_text_hash": f"fake:{text}",
        }


class FailingEmbedder:
    name = "failing_embedding"
    model = "failing-model"
    dimensions = None

    def embed(self, _text):
        raise RuntimeError("embedding failed")


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


def test_resolved_ingestion_epoch_uses_explicit_or_date_default() -> None:
    timestamp = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
    default_result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[IngestedNode(node_key="root", title="Root", text="Root text")],
        created_at=timestamp,
    )
    explicit_result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[IngestedNode(node_key="root", title="Root", text="Root text")],
        ingestion_epoch="2026-05-30-rs5-rebuild-001",
        created_at=timestamp,
    )

    assert resolved_ingestion_epoch(default_result) == "2026-05-30-default"
    assert resolved_ingestion_epoch(explicit_result) == "2026-05-30-rs5-rebuild-001"


def test_commit_ingestion_stamps_epoch_on_document_tree_nodes_and_provenance() -> None:
    db = FakeDb()
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[IngestedNode(node_key="root", title="Root", text="Root text")],
        ingestion_epoch="2026-05-30-test-001",
        created_at=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )

    inserted = commit_ingestion(db, result)

    assert inserted["ingestion_epoch"] == "2026-05-30-test-001"
    assert db.documents.rows[0]["ingestion_epoch"] == "2026-05-30-test-001"
    assert db.documents.rows[0]["source"]["checksum_sha256"] == "checksum"
    assert db.trees.rows[0]["ingestion_epoch"] == "2026-05-30-test-001"
    assert db.trees.rows[0]["status"] == "active"
    assert db.nodes.rows[0]["ingestion_epoch"] == "2026-05-30-test-001"
    assert db.nodes.rows[0]["status"] == "active"
    assert db.nodes.rows[0]["provenance"]["ingestion_epoch"] == "2026-05-30-test-001"


def test_commit_ingestion_persists_source_origin_date_metadata() -> None:
    db = FakeDb()
    result = IngestionResult(
        source=SourceRef(
            path="source.md",
            kind="markdown",
            checksum_sha256="checksum",
            origin_date="2020-01-01",
            origin_date_source="explicit_content",
            date_candidates=[
                {
                    "source": "explicit_content",
                    "date": "2020-01-01",
                    "raw": "2020",
                    "rationale": "Explicit date marker found in document content.",
                }
            ],
        ),
        title="Source",
        summary="Summary",
        nodes=[IngestedNode(node_key="root", title="Root", text="Root text")],
        created_at=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )

    commit_ingestion(db, result)

    source = db.documents.rows[0]["source"]
    assert source["origin_date"] == "2020-01-01"
    assert source["origin_date_source"] == "explicit_content"
    assert source["date_candidates"][0]["source"] == "explicit_content"


def test_commit_ingestion_annotates_nodes_with_embedding_metadata() -> None:
    db = FakeDb()
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[
            IngestedNode(node_key="root", title="Root", text="Root text"),
            IngestedNode(node_key="child", parent_key="root", title="Child", text="Child text"),
        ],
        created_at=datetime.now(timezone.utc),
    )

    inserted = commit_ingestion(db, result)

    assert inserted["embedded_node_count"] == 2
    assert inserted["embedding_adapter"] == "mock_embedding"
    assert inserted["embedding_dimensions"] == 16
    for row in db.nodes.rows:
        embedding = row["embedding"]
        assert embedding["adapter"] == "mock_embedding"
        assert embedding["model"] == "mock-deterministic-v1"
        assert embedding["dimensions"] == 16
        assert len(embedding["vector"]) == 16
        assert embedding["source_text_hash"].startswith("sha256:")
    # Embeddings are deterministic from the node text, not the node identity.
    root = next(row for row in db.nodes.rows if row["node_key"] == "root")
    child = next(row for row in db.nodes.rows if row["node_key"] == "child")
    assert root["embedding"]["vector"] != child["embedding"]["vector"]


def test_commit_ingestion_uses_supplied_embedder() -> None:
    from mnemosyne.adapters.embedding import MockEmbeddingAdapter

    db = FakeDb()
    result = IngestionResult(
        source=SourceRef(path="source.md", kind="markdown", checksum_sha256="checksum"),
        title="Source",
        summary="Summary",
        nodes=[IngestedNode(node_key="root", title="Root", text="Root text")],
        created_at=datetime.now(timezone.utc),
    )

    commit_ingestion(db, result, embedder=MockEmbeddingAdapter(dimensions=8))

    assert len(db.nodes.rows[0]["embedding"]["vector"]) == 8


def test_backfill_node_embeddings_updates_missing_embeddings_only() -> None:
    db = FakeDb()
    document_id = ObjectId()
    missing_id = ObjectId()
    existing_id = ObjectId()
    superseded_id = ObjectId()
    db.nodes.rows.extend(
        [
            {
                "_id": missing_id,
                "document_id": document_id,
                "title": "Missing",
                "text": "Missing embedding text",
                "labels": ["target"],
                "status": "active",
            },
            {
                "_id": existing_id,
                "document_id": document_id,
                "title": "Existing",
                "text": "Existing embedding text",
                "labels": ["target"],
                "status": "active",
                "embedding": {"adapter": "old", "vector": [1]},
            },
            {
                "_id": superseded_id,
                "document_id": document_id,
                "title": "Superseded",
                "text": "Superseded text",
                "labels": ["target"],
                "status": "superseded",
            },
        ]
    )

    result = backfill_node_embeddings(
        db,
        FakeEmbedder(),
        label="target",
        document_id=str(document_id),
        limit=10,
    )

    assert result["ok"] is True
    assert result["scanned_count"] == 1
    assert result["matched_count"] == 1
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 0
    assert result["last_node_id"] == str(missing_id)
    assert result["activity_log"].startswith("Embedding Backfill Activity Log")
    assert "1 node(s) embedded" in result["activity_log"]
    assert result["adapter"] == "fake_embedding"
    assert result["model"] == "fake-model"
    assert result["dimensions"] == 2
    missing = next(row for row in db.nodes.rows if row["_id"] == missing_id)
    existing = next(row for row in db.nodes.rows if row["_id"] == existing_id)
    superseded = next(row for row in db.nodes.rows if row["_id"] == superseded_id)
    assert missing["embedding"]["adapter"] == "fake_embedding"
    assert existing["embedding"]["adapter"] == "old"
    assert "embedding" not in superseded


def test_backfill_node_embeddings_force_replaces_existing_embeddings() -> None:
    db = FakeDb()
    node_id = ObjectId()
    db.nodes.rows.append(
        {
            "_id": node_id,
            "document_id": ObjectId(),
            "title": "Existing",
            "text": "Existing embedding text",
            "labels": ["target"],
            "status": "active",
            "embedding": {"adapter": "old", "vector": [1]},
        }
    )

    result = backfill_node_embeddings(db, FakeEmbedder(), label="target", force=True)

    assert result["updated_count"] == 1
    assert db.nodes.rows[0]["embedding"]["adapter"] == "fake_embedding"


def test_backfill_node_embeddings_collects_node_errors() -> None:
    db = FakeDb()
    for title in ("Bad 1", "Bad 2", "Bad 3"):
        db.nodes.rows.append(
            {
                "_id": ObjectId(),
                "document_id": ObjectId(),
                "title": title,
                "text": "bad",
                "status": "active",
            }
        )

    result = backfill_node_embeddings(db, FailingEmbedder(), max_errors=2)

    assert result["ok"] is False
    assert result["reason"] == "all_embedding_updates_failed"
    assert "needs attention" in result["activity_log"]
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 3
    assert result["error_count"] == 3
    assert result["error_sample_limit"] == 2
    assert [error["title"] for error in result["errors"]] == ["Bad 1", "Bad 2"]
    assert result["errors"][0]["error_type"] == "RuntimeError"


def test_backfill_node_embeddings_continues_after_node_id() -> None:
    db = FakeDb()
    first_id = ObjectId()
    second_id = ObjectId()
    db.nodes.rows.extend(
        [
            {"_id": first_id, "title": "First", "text": "first", "status": "active"},
            {"_id": second_id, "title": "Second", "text": "second", "status": "active"},
        ]
    )

    result = backfill_node_embeddings(db, FakeEmbedder(), after_node_id=str(first_id), limit=10)

    assert result["updated_count"] == 1
    assert result["last_node_id"] == str(second_id)
    assert "embedding" not in db.nodes.rows[0]
    assert db.nodes.rows[1]["embedding"]["adapter"] == "fake_embedding"


def test_backfill_node_embeddings_repairs_partial_embedding_without_vector() -> None:
    db = FakeDb()
    node_id = ObjectId()
    db.nodes.rows.append(
        {
            "_id": node_id,
            "title": "Partial",
            "text": "partial",
            "status": "active",
            "embedding": {"adapter": "old"},
        }
    )

    result = backfill_node_embeddings(db, FakeEmbedder())

    assert result["updated_count"] == 1
    assert db.nodes.rows[0]["embedding"]["vector"] == [1.0, 0.0]


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


def test_rebuild_document_inserts_versioned_tree_and_supersedes_previous_records() -> None:
    document_id = ObjectId()
    tree_id = ObjectId()
    node_id = ObjectId()
    db = FakeDb()
    db.documents.rows.append(
        {
            "_id": document_id,
            "title": "Old",
            "summary": "Old summary",
            "source": {"path": "old.md", "kind": "markdown"},
            "ingestion_epoch": "legacy",
        }
    )
    db.trees.rows.append(
        {
            "_id": tree_id,
            "document_id": document_id,
            "label": "source",
            "ingestion_epoch": "legacy",
        }
    )
    db.nodes.rows.append(
        {
            "_id": node_id,
            "document_id": document_id,
            "tree_id": tree_id,
            "node_key": "root",
            "title": "Old root",
            "ingestion_epoch": "legacy",
        }
    )
    result = IngestionResult(
        source=SourceRef(path="new.md", kind="markdown", checksum_sha256="new"),
        title="New",
        summary="New summary",
        nodes=[IngestedNode(node_key="root", title="New root", text="New text")],
        ingestion_epoch="2026-05-30-rebuild-001",
        created_at=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )

    inserted = rebuild_document(db, str(document_id), result)

    assert inserted["ingestion_epoch"] == "2026-05-30-rebuild-001"
    assert inserted["versioned"] is True
    assert inserted["superseded_tree_count"] == 1
    assert inserted["superseded_node_count"] == 1
    assert db.documents.rows[0]["ingestion_epoch"] == "2026-05-30-rebuild-001"
    assert len(db.trees.rows) == 2
    assert len(db.nodes.rows) == 2
    old_tree = next(row for row in db.trees.rows if row["_id"] == tree_id)
    old_node = next(row for row in db.nodes.rows if row["_id"] == node_id)
    new_tree = next(row for row in db.trees.rows if row["_id"] != tree_id)
    new_node = next(row for row in db.nodes.rows if row["_id"] != node_id)
    assert old_tree["status"] == "superseded"
    assert old_tree["superseded_by_epoch"] == "2026-05-30-rebuild-001"
    assert old_node["status"] == "superseded"
    assert old_node["superseded_by_epoch"] == "2026-05-30-rebuild-001"
    assert new_tree["ingestion_epoch"] == "2026-05-30-rebuild-001"
    assert new_tree["status"] == "active"
    assert new_node["ingestion_epoch"] == "2026-05-30-rebuild-001"
    assert new_node["status"] == "active"


def test_document_tree_returns_only_active_nodes() -> None:
    document_id = ObjectId()
    active_id = ObjectId()
    superseded_id = ObjectId()
    db = FakeDb()
    db.nodes.rows.extend(
        [
            {
                "_id": superseded_id,
                "document_id": document_id,
                "tree_id": ObjectId(),
                "node_key": "old",
                "parent_id": None,
                "order": 0,
                "title": "Old root",
                "labels": ["source_root"],
                "status": "superseded",
            },
            {
                "_id": active_id,
                "document_id": document_id,
                "tree_id": ObjectId(),
                "node_key": "new",
                "parent_id": None,
                "order": 1,
                "title": "New root",
                "labels": ["source_root"],
                "status": "active",
            },
        ]
    )

    tree = document_tree(db, str(document_id))

    assert [node["node_id"] for node in tree] == [str(active_id)]


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


def test_create_reviewed_semantic_edge_persists_user_reviewed_edge() -> None:
    db = FakeDb()
    source_id = ObjectId()
    target_id = ObjectId()
    source_document_id = ObjectId()
    target_document_id = ObjectId()
    db.nodes.rows.extend(
        [
            {
                "_id": source_id,
                "document_id": source_document_id,
                "tree_id": ObjectId(),
                "node_key": "source",
                "labels": ["source_chunk", "taj_mahal", "online_test"],
            },
            {
                "_id": target_id,
                "document_id": target_document_id,
                "tree_id": ObjectId(),
                "node_key": "target",
                "labels": ["source_chunk", "taj_mahal", "memory_reference"],
            },
        ]
    )

    result = create_reviewed_semantic_edge(
        db,
        source_node_id=str(source_id),
        target_node_id=str(target_id),
        relation_type="Related To",
        weight=2,
        confidence="bad",
        reviewer="cello",
        note="Shared Taj Mahal label.",
    )

    assert result["ok"] is True
    assert len(db.graph_edges.rows) == 1
    edge = db.graph_edges.rows[0]
    assert edge["source_node_id"] == source_id
    assert edge["target_node_id"] == target_id
    assert edge["source_document_id"] == source_document_id
    assert edge["target_document_id"] == target_document_id
    assert edge["relation_type"] == "related_to"
    assert edge["weight"] == 1.0
    assert edge["confidence"] == 0.6
    assert edge["provenance"]["source"] == "semantic_candidate_review"
    assert edge["provenance"]["reviewer"] == "cello"
    assert edge["provenance"]["shared_labels"] == ["taj_mahal"]

    duplicate = create_reviewed_semantic_edge(
        db,
        source_node_id=str(source_id),
        target_node_id=str(target_id),
        relation_type="related_to",
    )

    assert duplicate["ok"] is False
    assert duplicate["reason"] == "duplicate_edge"


def test_enqueue_semantic_edge_candidates_stores_pending_review_rows(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    document_id = ObjectId()
    db = FakeDb()
    db.nodes.rows.append(
        {
            "_id": source_id,
            "document_id": document_id,
            "tree_id": ObjectId(),
            "node_key": "source",
            "title": "Source",
            "labels": ["taj_mahal"],
        }
    )

    monkeypatch.setattr(
        "mnemosyne.retrieval.queries.semantic_candidate_nodes",
        lambda _db, node_id, limit=10, include_same_document=False: [
            {
                "node_id": str(target_id),
                "document_id": str(ObjectId()),
                "node_key": "target",
                "title": "Target",
                "shared_labels": ["taj_mahal"],
                "shared_label_count": 1,
            }
        ],
    )

    result = enqueue_semantic_edge_candidates(
        db,
        node_id=str(source_id),
        relation_type="Related To",
        created_by="cello",
    )

    assert result["ok"] is True
    assert result["candidate_count"] == 1
    assert result["enqueued_count"] == 1
    assert len(db.semantic_edge_candidates.rows) == 1
    row = db.semantic_edge_candidates.rows[0]
    assert row["status"] == "pending"
    assert row["source_node_id"] == source_id
    assert row["target_node_id"] == target_id
    assert row["source_document_id"] == document_id
    assert row["relation_type"] == "related_to"
    assert row["shared_labels"] == ["taj_mahal"]
    assert row["created_by"] == "cello"

    duplicate = enqueue_semantic_edge_candidates(db, node_id=str(source_id))

    assert duplicate["enqueued_count"] == 0
    assert duplicate["skipped_existing_count"] == 1


def test_enqueue_vector_semantic_edge_candidates_stores_similarity_review_rows(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    document_id = ObjectId()
    db = FakeDb()
    db.nodes.rows.append(
        {
            "_id": source_id,
            "document_id": document_id,
            "tree_id": ObjectId(),
            "node_key": "source",
            "title": "Source",
            "labels": ["taj_mahal"],
        }
    )

    monkeypatch.setattr(
        "mnemosyne.retrieval.queries.embedding_candidate_nodes",
        lambda _db, node_id, limit=10, include_same_document=False, min_similarity=0.75: [
            {
                "node_id": str(target_id),
                "document_id": str(ObjectId()),
                "node_key": "target",
                "title": "Target",
                "embedding_similarity": 0.91,
                "embedding_model": "mock",
                "embedding_dimensions": 16,
            }
        ],
    )

    result = enqueue_vector_semantic_edge_candidates(
        db,
        node_id=str(source_id),
        relation_type="Related To",
        created_by="cello",
        min_similarity=0.8,
    )

    assert result["ok"] is True
    assert result["candidate_source"] == "embedding_similarity"
    assert result["min_similarity"] == 0.8
    assert result["candidate_count"] == 1
    assert result["enqueued_count"] == 1
    assert len(db.semantic_edge_candidates.rows) == 1
    row = db.semantic_edge_candidates.rows[0]
    assert row["status"] == "pending"
    assert row["candidate_source"] == "embedding_similarity"
    assert row["source_node_id"] == source_id
    assert row["target_node_id"] == target_id
    assert row["source_document_id"] == document_id
    assert row["relation_type"] == "related_to"
    assert row["embedding_similarity"] == 0.91
    assert row["embedding_model"] == "mock"
    assert row["embedding_dimensions"] == 16
    assert row["selection_context"] == {
        "candidate_source": "embedding_similarity",
        "min_similarity": 0.8,
        "include_same_document": False,
        "requested_limit": 10,
        "embedding_model": "mock",
        "embedding_dimensions": 16,
    }
    assert row["created_by"] == "cello"

    duplicate = enqueue_vector_semantic_edge_candidates(db, node_id=str(source_id))

    assert duplicate["enqueued_count"] == 0
    assert duplicate["skipped_existing_count"] == 1


def test_list_semantic_edge_candidates_serializes_pending_rows() -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    timestamp = datetime(2026, 5, 28, tzinfo=timezone.utc)
    db = FakeDb()
    db.semantic_edge_candidates.rows.append(
        {
            "_id": ObjectId(),
            "status": "pending",
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relation_type": "related_to",
            "candidate_source": "embedding_similarity",
            "shared_labels": ["memory_reference"],
            "shared_label_count": 1,
            "embedding_similarity": 0.88,
            "embedding_model": "mock",
            "embedding_dimensions": 16,
            "selection_context": {
                "candidate_source": "embedding_similarity",
                "min_similarity": 0.75,
            },
            "source_title": "Source",
            "target_title": "Target",
            "created_by": "user",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )

    rows = list_semantic_edge_candidates(db)

    assert rows == [
        {
            "candidate_id": str(db.semantic_edge_candidates.rows[0]["_id"]),
            "status": "pending",
            "source_node_id": str(source_id),
            "target_node_id": str(target_id),
            "source_document_id": None,
            "target_document_id": None,
            "source_node_key": None,
            "target_node_key": None,
            "relation_type": "related_to",
            "candidate_source": "embedding_similarity",
            "shared_labels": ["memory_reference"],
            "shared_label_count": 1,
            "embedding_similarity": 0.88,
            "embedding_model": "mock",
            "embedding_dimensions": 16,
            "selection_context": {
                "candidate_source": "embedding_similarity",
                "min_similarity": 0.75,
            },
            "source_title": "Source",
            "target_title": "Target",
            "created_by": "user",
            "reviewer": None,
            "review_note": None,
            "edge_id": None,
            "created_at": "2026-05-28T00:00:00+00:00",
            "updated_at": "2026-05-28T00:00:00+00:00",
            "reviewed_at": None,
        }
    ]


def test_review_semantic_edge_candidate_accepts_and_creates_edge() -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    candidate_id = ObjectId()
    db = FakeDb()
    db.nodes.rows.extend(
        [
            {
                "_id": source_id,
                "document_id": ObjectId(),
                "tree_id": ObjectId(),
                "node_key": "source",
                "labels": ["memory_reference"],
            },
            {
                "_id": target_id,
                "document_id": ObjectId(),
                "tree_id": ObjectId(),
                "node_key": "target",
                "labels": ["memory_reference"],
            },
        ]
    )
    db.semantic_edge_candidates.rows.append(
        {
            "_id": candidate_id,
            "status": "pending",
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relation_type": "related_to",
            "candidate_source": "embedding_similarity",
            "embedding_similarity": 0.91,
            "embedding_model": "mock",
            "embedding_dimensions": 16,
            "selection_context": {
                "candidate_source": "embedding_similarity",
                "min_similarity": 0.8,
                "embedding_model": "mock",
                "embedding_dimensions": 16,
            },
        }
    )

    result = review_semantic_edge_candidate(
        db,
        candidate_id=str(candidate_id),
        action="accept",
        reviewer="cello",
        note="Looks related.",
        weight=0.8,
        confidence=0.9,
    )

    assert result["ok"] is True
    assert len(db.graph_edges.rows) == 1
    assert result["candidate"]["status"] == "accepted"
    assert result["candidate"]["reviewer"] == "cello"
    assert result["candidate"]["review_note"] == "Looks related."
    assert result["candidate"]["edge_id"] == result["edge"]["edge_id"]
    assert result["edge"]["weight"] == 0.8
    assert result["edge"]["confidence"] == 0.9
    assert result["edge"]["provenance"]["candidate_source"] == "embedding_similarity"
    assert result["edge"]["provenance"]["embedding_similarity"] == 0.91
    assert result["edge"]["provenance"]["embedding_model"] == "mock"
    assert result["edge"]["provenance"]["embedding_dimensions"] == 16
    assert result["edge"]["provenance"]["selection_context"] == {
        "candidate_source": "embedding_similarity",
        "min_similarity": 0.8,
        "embedding_model": "mock",
        "embedding_dimensions": 16,
    }


def test_review_semantic_edge_candidate_rejects_pending_candidate() -> None:
    candidate_id = ObjectId()
    db = FakeDb()
    db.semantic_edge_candidates.rows.append(
        {
            "_id": candidate_id,
            "status": "pending",
            "source_node_id": ObjectId(),
            "target_node_id": ObjectId(),
            "relation_type": "related_to",
        }
    )

    result = review_semantic_edge_candidate(
        db,
        candidate_id=str(candidate_id),
        action="reject",
        reviewer="cello",
        note="Too broad.",
    )

    assert result["ok"] is True
    assert result["candidate"]["status"] == "rejected"
    assert result["candidate"]["reviewer"] == "cello"
    assert result["candidate"]["review_note"] == "Too broad."
    assert db.graph_edges.rows == []


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


class FakeCursor(list):
    def sort(self, field, direction=1):
        super().sort(key=lambda row: mongo_sort_value(nested_get(row, field)), reverse=direction < 0)
        return self

    def limit(self, value):
        return FakeCursor(self[:value])


class FakeCollection:
    def __init__(self, fail_insert=False, fail_insert_many=False):
        self.rows = []
        self.fail_insert = fail_insert
        self.fail_insert_many = fail_insert_many

    def find_one(self, query):
        row = next((row for row in self.rows if matches(row, query)), None)
        return dict(row) if row else None

    def find(self, query, _projection=None):
        return FakeCursor([dict(row) for row in self.rows if matches(row, query)])

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
            row = dict(row)
            row.setdefault("_id", ObjectId())
            self.rows.append(row)
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
        self.semantic_edge_candidates = FakeCollection()


def matches(row, query):
    for key, expected in query.items():
        actual = nested_get(row, key)
        if isinstance(expected, dict):
            if "$exists" in expected and (actual is not None) is not expected["$exists"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                return False
            continue
        if isinstance(actual, list):
            if expected not in actual:
                return False
            continue
        if actual != expected:
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
