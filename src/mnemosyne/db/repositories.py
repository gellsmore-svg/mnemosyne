from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from mnemosyne.adapters.embedding import default_embedding_adapter
from mnemosyne.models.ingestion import (
    DEFAULT_ENDORSEMENT_LABEL,
    SCHEMA_VERSION,
    DocumentRecord,
    IngestionResult,
    NodeRecord,
    Provenance,
    TreeRecord,
)

STRUCTURAL_LABELS = {"source_root", "source_section", "source_chunk"}
DEFAULT_INGESTION_EPOCH_SUFFIX = "default"


class DuplicateSourceError(Exception):
    def __init__(self, checksum: str, existing_document_id: object) -> None:
        super().__init__(f"Duplicate source checksum: {checksum}")
        self.checksum = checksum
        self.existing_document_id = existing_document_id


def find_duplicate_by_checksum(db: Database, checksum: str) -> dict | None:
    return db.documents.find_one({"source.checksum_sha256": checksum})


def commit_ingestion(
    db: Database,
    result: IngestionResult,
    embedder: Any | None = None,
) -> dict[str, Any]:
    if result.source.checksum_sha256:
        existing = find_duplicate_by_checksum(db, result.source.checksum_sha256)
        if existing:
            raise DuplicateSourceError(result.source.checksum_sha256, existing["_id"])

    ingestion_epoch = resolved_ingestion_epoch(result)
    document = DocumentRecord(
        title=result.title,
        summary=result.summary,
        source=result.source,
        ingestion_epoch=ingestion_epoch,
        created_at=result.created_at,
        updated_at=result.created_at,
    ).model_dump()
    document_id = db.documents.insert_one(document).inserted_id

    try:
        inserted = insert_tree_nodes(
            db, document_id, result, ingestion_epoch=ingestion_epoch, embedder=embedder
        )
    except Exception:
        delete_graph_edges_for_document(db, document_id)
        db.nodes.delete_many({"document_id": document_id})
        db.trees.delete_many({"document_id": document_id})
        db.documents.delete_one({"_id": document_id})
        raise

    return {
        "document_id": str(document_id),
        **inserted,
    }


def rebuild_document(
    db: Database,
    document_id: str,
    result: IngestionResult,
    embedder: Any | None = None,
) -> dict[str, Any]:
    object_id = ObjectId(document_id)
    existing = db.documents.find_one({"_id": object_id})
    if not existing:
        raise ValueError(f"Document not found: {document_id}")

    ingestion_epoch = resolved_ingestion_epoch(result)
    previous_trees = list(db.trees.find({"document_id": object_id}))
    previous_nodes = list(db.nodes.find({"document_id": object_id}))
    previous_edges = list_graph_edges_for_document(db, object_id)
    try:
        mark_document_tree_nodes_status(
            db,
            document_id=object_id,
            status="superseded",
            superseded_by_epoch=ingestion_epoch,
        )
        db.documents.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "title": result.title,
                    "summary": result.summary,
                    "source": result.source.model_dump(),
                    "ingestion_epoch": ingestion_epoch,
                    "updated_at": result.created_at,
                }
            },
        )
        inserted = insert_tree_nodes(
            db, object_id, result, ingestion_epoch=ingestion_epoch, embedder=embedder
        )
    except Exception:
        db.documents.replace_one({"_id": object_id}, existing)
        restore_collection_rows(db.trees, object_id, previous_trees)
        restore_collection_rows(db.nodes, object_id, previous_nodes)
        delete_graph_edges_for_document(db, object_id)
        if previous_edges and hasattr(db, "graph_edges"):
            db.graph_edges.insert_many(previous_edges)
        raise
    return {
        "document_id": str(object_id),
        "replaced": False,
        "versioned": True,
        "superseded_tree_count": len(previous_trees),
        "superseded_node_count": len(previous_nodes),
        **inserted,
    }


def resolved_ingestion_epoch(result: IngestionResult) -> str:
    if result.ingestion_epoch:
        return result.ingestion_epoch
    created_at = result.created_at
    return f"{created_at:%Y-%m-%d}-{DEFAULT_INGESTION_EPOCH_SUFFIX}"


def mark_document_tree_nodes_status(
    db: Database,
    *,
    document_id: object,
    status: str,
    superseded_by_epoch: str | None = None,
) -> None:
    set_fields: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
    }
    if superseded_by_epoch:
        set_fields["superseded_by_epoch"] = superseded_by_epoch
    for collection in (db.trees, db.nodes):
        for row in collection.find({"document_id": document_id}):
            collection.update_one({"_id": row["_id"]}, {"$set": set_fields})


def restore_collection_rows(
    collection: Any,
    document_id: object,
    previous_rows: list[dict[str, Any]],
) -> None:
    collection.delete_many({"document_id": document_id})
    if previous_rows:
        collection.insert_many(previous_rows)


def insert_tree_nodes(
    db: Database,
    document_id: object,
    result: IngestionResult,
    *,
    ingestion_epoch: str | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    ingestion_epoch = ingestion_epoch or resolved_ingestion_epoch(result)
    embedder = embedder or default_embedding_adapter()
    tree = TreeRecord(
        document_id=document_id,
        label=result.tree_label,
        ingestion_epoch=ingestion_epoch,
        created_at=result.created_at,
        updated_at=result.created_at,
    )
    tree_doc = tree.model_dump()
    tree_id = db.trees.insert_one(tree_doc).inserted_id

    key_to_id: dict[str, object] = {}
    node_ids = []
    embedded_node_count = 0
    for order, node in enumerate(result.nodes):
        endorsement_label = node.endorsement_label or DEFAULT_ENDORSEMENT_LABEL
        parent_id = key_to_id.get(node.parent_key) if node.parent_key else None
        embedding = embedder.embed(node.text)
        embedded_node_count += 1
        node_record = NodeRecord(
            document_id=document_id,
            tree_id=tree_id,
            parent_id=parent_id,
            node_key=node.node_key,
            parent_key=node.parent_key,
            order=order,
            title=node.title,
            text=node.text,
            summary=node.summary or summarize_node_text(node.text),
            labels=node.labels,
            endorsement_label=endorsement_label,
            relations=node.relations,
            proximity=node.proximity,
            usage_score=node.usage_score,
            continuity_critical=node.continuity_critical,
            ingestion_epoch=ingestion_epoch,
            provenance=Provenance(
                source_path=result.source.path,
                source_checksum_sha256=result.source.checksum_sha256,
                archive_path=result.source.archive_path,
                ingestion_epoch=ingestion_epoch,
                endorsement_label=endorsement_label,
                adapter=result.adapter,
            ),
            embedding=embedding,
            metadata=node.metadata,
            created_at=result.created_at,
            updated_at=result.created_at,
        )
        node_doc = node_record.model_dump()
        inserted_id = db.nodes.insert_one(node_doc).inserted_id
        key_to_id[node.node_key] = inserted_id
        node_ids.append(inserted_id)
    edge_result = insert_relation_edges(
        db=db,
        document_id=document_id,
        tree_id=tree_id,
        result=result,
        key_to_id=key_to_id,
    )

    return {
        "tree_id": str(tree_id),
        "node_ids": [str(node_id) for node_id in node_ids],
        "ingestion_epoch": ingestion_epoch,
        "embedded_node_count": embedded_node_count,
        "embedding_adapter": embedder.name,
        "embedding_model": embedder.model,
        "embedding_dimensions": getattr(embedder, "dimensions", None),
        **edge_result,
    }


def insert_relation_edges(
    db: Database,
    document_id: object,
    tree_id: object,
    result: IngestionResult,
    key_to_id: dict[str, object],
) -> dict[str, int]:
    if not hasattr(db, "graph_edges"):
        return {"edge_count": 0, "skipped_edge_count": 0}
    edge_docs = []
    seen_edges = set()
    skipped = 0
    for node in result.nodes:
        source_id = key_to_id.get(node.node_key)
        if not source_id:
            continue
        for relation in node.relations:
            edge = relation_edge_doc(
                relation=relation,
                source_node_key=node.node_key,
                source_node_id=source_id,
                key_to_id=key_to_id,
                document_id=document_id,
                tree_id=tree_id,
                adapter=result.adapter,
                created_at=result.created_at,
            )
            if edge:
                edge_key = (
                    edge["source_node_id"],
                    edge["target_node_id"],
                    edge["relation_type"],
                )
                if edge_key in seen_edges:
                    skipped += 1
                    continue
                seen_edges.add(edge_key)
                edge_docs.append(edge)
            else:
                skipped += 1
    if edge_docs:
        db.graph_edges.insert_many(edge_docs)
    return {"edge_count": len(edge_docs), "skipped_edge_count": skipped}


def relation_edge_doc(
    relation: dict[str, Any],
    source_node_key: str,
    source_node_id: object,
    key_to_id: dict[str, object],
    document_id: object,
    tree_id: object,
    adapter: str,
    created_at,
) -> dict[str, Any] | None:
    target_node_key = relation_target_key(relation)
    relation_type = relation_type_value(relation)
    if not target_node_key or not relation_type:
        return None
    target_node_id = key_to_id.get(target_node_key)
    if not target_node_id:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "tree_id": tree_id,
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "source_node_key": source_node_key,
        "target_node_key": target_node_key,
        "relation_type": relation_type,
        "weight": relation_weight(relation),
        "confidence": relation.get("confidence"),
        "direction": relation.get("direction") or "directed",
        "provenance": {
            "adapter": adapter,
            "source": "ingestion_node_relation",
            "raw_relation": relation,
        },
        "created_at": created_at,
        "updated_at": created_at,
    }


def relation_target_key(relation: dict[str, Any]) -> str | None:
    value = (
        relation.get("target_node_key")
        or relation.get("target_key")
        or relation.get("node_key")
        or relation.get("target")
    )
    return str(value) if value else None


def relation_type_value(relation: dict[str, Any]) -> str | None:
    value = relation.get("relation_type") or relation.get("type") or relation.get("label")
    return str(value) if value else None


def relation_weight(relation: dict[str, Any]) -> float:
    try:
        return float(relation.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0


def delete_graph_edges_for_document(db: Database, document_id: object) -> None:
    if hasattr(db, "graph_edges"):
        db.graph_edges.delete_many({"document_id": document_id})


def list_graph_edges_for_document(db: Database, document_id: object) -> list[dict[str, Any]]:
    if not hasattr(db, "graph_edges"):
        return []
    return list(db.graph_edges.find({"document_id": document_id}))


def graph_edge_status(db: Database, limit: int = 10) -> dict[str, Any]:
    if not hasattr(db, "graph_edges"):
        return {
            "edge_count": 0,
            "relation_types": [],
            "provenance_sources": [],
        }
    return {
        "edge_count": db.graph_edges.count_documents({}),
        "relation_types": graph_edge_group_counts(db, "$relation_type", limit=limit),
        "provenance_sources": graph_edge_group_counts(db, "$provenance.source", limit=limit),
    }


def graph_edge_group_counts(db: Database, field: str, limit: int = 10) -> list[dict[str, Any]]:
    group_limit = bounded_graph_group_limit(limit)
    rows = db.graph_edges.aggregate(
        [
            {"$group": {"_id": field, "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
            {"$limit": group_limit},
        ]
    )
    return [
        {
            "value": row.get("_id"),
            "count": row.get("count", 0),
        }
        for row in rows
    ]


def bounded_graph_group_limit(value: Any, default: int = 10) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 50))


def backfill_structural_graph_edges(db: Database, limit: int | None = None) -> dict[str, int]:
    if not hasattr(db, "graph_edges"):
        return {
            "scanned_node_count": 0,
            "edge_count": 0,
            "skipped_existing_count": 0,
            "skipped_missing_parent_count": 0,
        }
    edge_docs = []
    scanned = 0
    skipped_existing = 0
    skipped_missing_parent = 0
    for child in db.nodes.find({"parent_id": {"$exists": True, "$ne": None}}):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        parent = db.nodes.find_one({"_id": child.get("parent_id")})
        if not parent:
            skipped_missing_parent += 1
            continue
        if structural_edge_exists(db, parent["_id"], child["_id"]):
            skipped_existing += 1
            continue
        edge_docs.append(structural_edge_doc(parent, child))
    if edge_docs:
        db.graph_edges.insert_many(edge_docs)
    return {
        "scanned_node_count": scanned,
        "edge_count": len(edge_docs),
        "skipped_existing_count": skipped_existing,
        "skipped_missing_parent_count": skipped_missing_parent,
    }


def structural_edge_exists(db: Database, parent_id: object, child_id: object) -> bool:
    return (
        db.graph_edges.find_one(
            {
                "source_node_id": parent_id,
                "target_node_id": child_id,
                "relation_type": "contains",
            }
        )
        is not None
    )


def structural_edge_doc(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    timestamp = child.get("created_at") or parent.get("created_at") or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": child.get("document_id") or parent.get("document_id"),
        "tree_id": child.get("tree_id") or parent.get("tree_id"),
        "source_node_id": parent["_id"],
        "target_node_id": child["_id"],
        "source_node_key": parent.get("node_key"),
        "target_node_key": child.get("node_key"),
        "relation_type": "contains",
        "weight": 1.0,
        "confidence": 1.0,
        "direction": "directed",
        "provenance": {
            "adapter": "structural_backfill",
            "source": "node_parent_link",
        },
        "created_at": timestamp,
        "updated_at": datetime.now(timezone.utc),
    }


def create_reviewed_semantic_edge(
    db: Database,
    source_node_id: str,
    target_node_id: str,
    relation_type: str = "related_to",
    weight: Any = 0.7,
    confidence: Any = 0.6,
    reviewer: str = "user",
    note: str | None = None,
    candidate_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not hasattr(db, "graph_edges"):
        return {"ok": False, "reason": "graph_edges_unavailable"}
    source_id = parse_object_id(source_node_id)
    target_id = parse_object_id(target_node_id)
    if source_id is None:
        return {"ok": False, "reason": "invalid_source_node_id", "source_node_id": source_node_id}
    if target_id is None:
        return {"ok": False, "reason": "invalid_target_node_id", "target_node_id": target_node_id}
    if source_id == target_id:
        return {"ok": False, "reason": "self_edge_not_allowed", "source_node_id": source_node_id}
    relation = normalized_relation_type(relation_type)
    if not relation:
        return {"ok": False, "reason": "invalid_relation_type", "relation_type": relation_type}

    source = db.nodes.find_one({"_id": source_id})
    if not source:
        return {"ok": False, "reason": "source_node_not_found", "source_node_id": source_node_id}
    target = db.nodes.find_one({"_id": target_id})
    if not target:
        return {"ok": False, "reason": "target_node_not_found", "target_node_id": target_node_id}

    duplicate = db.graph_edges.find_one(
        {
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relation_type": relation,
        }
    )
    if duplicate:
        return {
            "ok": False,
            "reason": "duplicate_edge",
            "edge_id": str(duplicate.get("_id")) if duplicate.get("_id") else None,
        }

    now = datetime.now(timezone.utc)
    shared_labels = shared_semantic_labels(source, target)
    provenance = {
        "adapter": "user_review",
        "source": "semantic_candidate_review",
        "reviewer": reviewer,
        "note": note,
        "shared_labels": shared_labels,
        "shared_label_count": len(shared_labels),
    }
    if candidate_context:
        provenance["candidate_source"] = candidate_context.get("candidate_source")
        provenance["selection_context"] = candidate_context.get("selection_context") or {}
        if candidate_context.get("embedding_similarity") is not None:
            provenance["embedding_similarity"] = candidate_context.get("embedding_similarity")
        if candidate_context.get("embedding_model"):
            provenance["embedding_model"] = candidate_context.get("embedding_model")
        if candidate_context.get("embedding_dimensions"):
            provenance["embedding_dimensions"] = candidate_context.get("embedding_dimensions")
    edge_doc = {
        "schema_version": SCHEMA_VERSION,
        "document_id": source.get("document_id"),
        "tree_id": source.get("tree_id"),
        "source_document_id": source.get("document_id"),
        "target_document_id": target.get("document_id"),
        "source_tree_id": source.get("tree_id"),
        "target_tree_id": target.get("tree_id"),
        "source_node_id": source_id,
        "target_node_id": target_id,
        "source_node_key": source.get("node_key"),
        "target_node_key": target.get("node_key"),
        "relation_type": relation,
        "weight": bounded_edge_score(weight, default=0.7),
        "confidence": bounded_edge_score(confidence, default=0.6),
        "direction": "directed",
        "provenance": provenance,
        "created_at": now,
        "updated_at": now,
    }
    inserted_id = db.graph_edges.insert_one(edge_doc).inserted_id
    edge_doc["_id"] = inserted_id
    return {
        "ok": True,
        "edge": {
            "edge_id": str(inserted_id),
            "source_node_id": str(source_id),
            "target_node_id": str(target_id),
            "relation_type": relation,
            "weight": edge_doc["weight"],
            "confidence": edge_doc["confidence"],
            "provenance": edge_doc["provenance"],
        },
    }


def enqueue_semantic_edge_candidates(
    db: Database,
    node_id: str,
    limit: int = 10,
    include_same_document: bool = False,
    relation_type: str = "related_to",
    created_by: str = "user",
) -> dict[str, Any]:
    if not hasattr(db, "semantic_edge_candidates"):
        return {"ok": False, "reason": "semantic_edge_candidates_unavailable"}
    source_id = parse_object_id(node_id)
    if source_id is None:
        return {"ok": False, "reason": "invalid_source_node_id", "source_node_id": node_id}
    source = db.nodes.find_one({"_id": source_id})
    if not source:
        return {"ok": False, "reason": "source_node_not_found", "source_node_id": node_id}
    relation = normalized_relation_type(relation_type)
    if not relation:
        return {"ok": False, "reason": "invalid_relation_type", "relation_type": relation_type}

    from mnemosyne.retrieval.queries import semantic_candidate_nodes

    candidates = semantic_candidate_nodes(
        db,
        node_id=node_id,
        limit=bounded_candidate_limit(limit),
        include_same_document=include_same_document,
    )
    now = datetime.now(timezone.utc)
    inserted = []
    skipped_existing = 0
    skipped_invalid = 0
    for candidate in candidates:
        target_id = parse_object_id(candidate.get("node_id"))
        if target_id is None:
            skipped_invalid += 1
            continue
        existing = db.semantic_edge_candidates.find_one(
            {
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation_type": relation,
            }
        )
        if existing:
            skipped_existing += 1
            continue
        inserted.append(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "pending",
                "source_node_id": source_id,
                "target_node_id": target_id,
                "source_document_id": source.get("document_id"),
                "target_document_id": parse_object_id(candidate.get("document_id")),
                "source_node_key": source.get("node_key"),
                "target_node_key": candidate.get("node_key"),
                "relation_type": relation,
                "shared_labels": candidate.get("shared_labels") or [],
                "shared_label_count": candidate.get("shared_label_count") or 0,
                "source_title": source.get("title"),
                "target_title": candidate.get("title"),
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
        )
    if inserted:
        db.semantic_edge_candidates.insert_many(inserted)
    return {
        "ok": True,
        "source_node_id": str(source_id),
        "candidate_source": "label_overlap",
        "candidate_count": len(candidates),
        "enqueued_count": len(inserted),
        "skipped_existing_count": skipped_existing,
        "skipped_invalid_count": skipped_invalid,
    }


def enqueue_vector_semantic_edge_candidates(
    db: Database,
    node_id: str,
    limit: int = 10,
    include_same_document: bool = False,
    relation_type: str = "related_to",
    created_by: str = "user",
    min_similarity: float = 0.75,
    candidate_scan_limit: int | None = None,
) -> dict[str, Any]:
    if not hasattr(db, "semantic_edge_candidates"):
        return {"ok": False, "reason": "semantic_edge_candidates_unavailable"}
    source_id = parse_object_id(node_id)
    if source_id is None:
        return {"ok": False, "reason": "invalid_source_node_id", "source_node_id": node_id}
    source = db.nodes.find_one({"_id": source_id})
    if not source:
        return {"ok": False, "reason": "source_node_not_found", "source_node_id": node_id}
    relation = normalized_relation_type(relation_type)
    if not relation:
        return {"ok": False, "reason": "invalid_relation_type", "relation_type": relation_type}

    try:
        threshold = float(min_similarity)
    except (TypeError, ValueError):
        threshold = 0.75
    threshold = max(-1.0, min(threshold, 1.0))

    from mnemosyne.retrieval.queries import embedding_candidate_nodes

    candidates = embedding_candidate_nodes(
        db,
        node_id=node_id,
        limit=bounded_candidate_limit(limit),
        include_same_document=include_same_document,
        min_similarity=threshold,
        candidate_scan_limit=candidate_scan_limit,
    )
    now = datetime.now(timezone.utc)
    inserted = []
    skipped_existing = 0
    skipped_invalid = 0
    for candidate in candidates:
        target_id = parse_object_id(candidate.get("node_id"))
        if target_id is None:
            skipped_invalid += 1
            continue
        existing = db.semantic_edge_candidates.find_one(
            {
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation_type": relation,
            }
        )
        if existing:
            skipped_existing += 1
            continue
        inserted.append(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "pending",
                "candidate_source": "embedding_similarity",
                "source_node_id": source_id,
                "target_node_id": target_id,
                "source_document_id": source.get("document_id"),
                "target_document_id": parse_object_id(candidate.get("document_id")),
                "source_node_key": source.get("node_key"),
                "target_node_key": candidate.get("node_key"),
                "relation_type": relation,
                "shared_labels": candidate.get("shared_labels") or [],
                "shared_label_count": candidate.get("shared_label_count") or 0,
                "embedding_similarity": candidate.get("embedding_similarity"),
                "embedding_model": candidate.get("embedding_model"),
                "embedding_dimensions": candidate.get("embedding_dimensions"),
                "selection_context": {
                    "candidate_source": "embedding_similarity",
                    "min_similarity": threshold,
                    "include_same_document": include_same_document,
                    "requested_limit": bounded_candidate_limit(limit),
                    "embedding_model": candidate.get("embedding_model"),
                    "embedding_dimensions": candidate.get("embedding_dimensions"),
                },
                "source_title": source.get("title"),
                "target_title": candidate.get("title"),
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
        )
    if inserted:
        db.semantic_edge_candidates.insert_many(inserted)
    return {
        "ok": True,
        "source_node_id": str(source_id),
        "candidate_source": "embedding_similarity",
        "min_similarity": threshold,
        "candidate_count": len(candidates),
        "enqueued_count": len(inserted),
        "skipped_existing_count": skipped_existing,
        "skipped_invalid_count": skipped_invalid,
    }


def enqueue_vector_semantic_edge_candidate_batch(
    db: Database,
    label: str | None = None,
    document_id: str | None = None,
    focus_limit: int = 25,
    candidates_per_node: int = 2,
    include_same_document: bool = False,
    relation_type: str = "related_to",
    created_by: str = "user",
    min_similarity: float = 0.75,
    candidate_scan_limit: int | None = None,
) -> dict[str, Any]:
    if not hasattr(db, "semantic_edge_candidates"):
        return {"ok": False, "reason": "semantic_edge_candidates_unavailable"}
    relation = normalized_relation_type(relation_type)
    if not relation:
        return {"ok": False, "reason": "invalid_relation_type", "relation_type": relation_type}

    filters: dict[str, Any] = {
        "embedding.model": {"$exists": True},
        "embedding.dimensions": {"$exists": True},
    }
    if label:
        filters["labels"] = label
    if document_id:
        parsed_document_id = parse_object_id(document_id)
        if parsed_document_id is None:
            return {"ok": False, "reason": "invalid_document_id", "document_id": document_id}
        filters["document_id"] = parsed_document_id

    try:
        threshold = float(min_similarity)
    except (TypeError, ValueError):
        threshold = 0.75
    threshold = max(-1.0, min(threshold, 1.0))
    bounded_focus_limit = max(1, min(int(focus_limit or 25), 200))
    bounded_candidates_per_node = bounded_candidate_limit(candidates_per_node)
    focus_nodes = []
    for node in db.nodes.find(filters).limit(bounded_focus_limit):
        if "source_root" in (node.get("labels") or []):
            continue
        if node.get("status") == "superseded":
            continue
        focus_nodes.append(node)

    totals = {
        "candidate_count": 0,
        "enqueued_count": 0,
        "skipped_existing_count": 0,
        "skipped_invalid_count": 0,
    }
    focus_results = []
    for node in focus_nodes:
        result = enqueue_vector_semantic_edge_candidates(
            db,
            node_id=str(node.get("_id")),
            limit=bounded_candidates_per_node,
            include_same_document=include_same_document,
            relation_type=relation,
            created_by=created_by,
            min_similarity=threshold,
            candidate_scan_limit=candidate_scan_limit,
        )
        for key in totals:
            totals[key] += int(result.get(key) or 0)
        focus_results.append(
            {
                "node_id": str(node.get("_id")),
                "title": node.get("title"),
                "ok": result.get("ok"),
                "candidate_count": result.get("candidate_count", 0),
                "enqueued_count": result.get("enqueued_count", 0),
                "skipped_existing_count": result.get("skipped_existing_count", 0),
                "skipped_invalid_count": result.get("skipped_invalid_count", 0),
                "reason": result.get("reason"),
            }
        )

    return {
        "ok": True,
        "candidate_source": "embedding_similarity",
        "scope": {
            "label": label,
            "document_id": document_id,
            "focus_limit": bounded_focus_limit,
            "focus_node_count": len(focus_nodes),
            "candidates_per_node": bounded_candidates_per_node,
            "include_same_document": include_same_document,
            "relation_type": relation,
            "created_by": created_by,
            "min_similarity": threshold,
            "candidate_scan_limit": candidate_scan_limit,
        },
        **totals,
        "focus_results": focus_results,
    }


def list_semantic_edge_candidates(
    db: Database,
    status: str | None = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not hasattr(db, "semantic_edge_candidates"):
        return []
    query = {}
    if status:
        query["status"] = status
    rows = db.semantic_edge_candidates.find(query).sort("created_at", -1).limit(
        bounded_candidate_limit(limit, maximum=100)
    )
    return [serialize_semantic_edge_candidate(row) for row in rows]


def review_semantic_edge_candidate(
    db: Database,
    candidate_id: str,
    action: str,
    reviewer: str = "user",
    note: str | None = None,
    weight: Any = 0.7,
    confidence: Any = 0.6,
) -> dict[str, Any]:
    if not hasattr(db, "semantic_edge_candidates"):
        return {"ok": False, "reason": "semantic_edge_candidates_unavailable"}
    object_id = parse_object_id(candidate_id)
    if object_id is None:
        return {"ok": False, "reason": "invalid_candidate_id", "candidate_id": candidate_id}
    candidate = db.semantic_edge_candidates.find_one({"_id": object_id})
    if not candidate:
        return {"ok": False, "reason": "candidate_not_found", "candidate_id": candidate_id}
    if candidate.get("status") != "pending":
        return {
            "ok": False,
            "reason": "candidate_not_pending",
            "candidate": serialize_semantic_edge_candidate(candidate),
        }

    normalized_action = str(action or "").strip().lower()
    if normalized_action == "reject":
        updated = update_semantic_edge_candidate_status(
            db,
            object_id,
            status="rejected",
            reviewer=reviewer,
            note=note,
        )
        return {"ok": True, "candidate": serialize_semantic_edge_candidate(updated)}
    if normalized_action != "accept":
        return {"ok": False, "reason": "invalid_action", "action": action}

    edge_result = create_reviewed_semantic_edge(
        db,
        source_node_id=str(candidate.get("source_node_id")),
        target_node_id=str(candidate.get("target_node_id")),
        relation_type=candidate.get("relation_type") or "related_to",
        weight=weight,
        confidence=confidence,
        reviewer=reviewer,
        note=note,
        candidate_context={
            "candidate_source": candidate.get("candidate_source") or "label_overlap",
            "selection_context": candidate.get("selection_context") or {},
            "embedding_similarity": candidate.get("embedding_similarity"),
            "embedding_model": candidate.get("embedding_model"),
            "embedding_dimensions": candidate.get("embedding_dimensions"),
        },
    )
    if not edge_result.get("ok"):
        return {
            "ok": False,
            "reason": "edge_creation_failed",
            "edge_result": edge_result,
            "candidate": serialize_semantic_edge_candidate(candidate),
        }
    updated = update_semantic_edge_candidate_status(
        db,
        object_id,
        status="accepted",
        reviewer=reviewer,
        note=note,
        edge_id=edge_result.get("edge", {}).get("edge_id"),
    )
    return {
        "ok": True,
        "edge": edge_result.get("edge"),
        "candidate": serialize_semantic_edge_candidate(updated),
    }


def update_semantic_edge_candidate_status(
    db: Database,
    candidate_id: ObjectId,
    status: str,
    reviewer: str,
    note: str | None,
    edge_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    review = {
        "action": status,
        "reviewer": reviewer,
        "note": note,
        "reviewed_at": now,
    }
    updates: dict[str, Any] = {
        "status": status,
        "reviewer": reviewer,
        "review_note": note,
        "reviewed_at": now,
        "updated_at": now,
        "review": review,
    }
    if edge_id:
        updates["edge_id"] = edge_id
    db.semantic_edge_candidates.update_one({"_id": candidate_id}, {"$set": updates})
    return db.semantic_edge_candidates.find_one({"_id": candidate_id}) or {}


def serialize_semantic_edge_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(row["_id"]) if row.get("_id") else None,
        "status": row.get("status"),
        "source_node_id": str(row["source_node_id"]) if row.get("source_node_id") else None,
        "target_node_id": str(row["target_node_id"]) if row.get("target_node_id") else None,
        "source_document_id": str(row["source_document_id"])
        if row.get("source_document_id")
        else None,
        "target_document_id": str(row["target_document_id"])
        if row.get("target_document_id")
        else None,
        "source_node_key": row.get("source_node_key"),
        "target_node_key": row.get("target_node_key"),
        "relation_type": row.get("relation_type"),
        "candidate_source": row.get("candidate_source") or "label_overlap",
        "shared_labels": row.get("shared_labels") or [],
        "shared_label_count": row.get("shared_label_count") or 0,
        "embedding_similarity": row.get("embedding_similarity"),
        "embedding_model": row.get("embedding_model"),
        "embedding_dimensions": row.get("embedding_dimensions"),
        "selection_context": row.get("selection_context") or {},
        "source_title": row.get("source_title"),
        "target_title": row.get("target_title"),
        "created_by": row.get("created_by"),
        "reviewer": row.get("reviewer"),
        "review_note": row.get("review_note"),
        "edge_id": row.get("edge_id"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        "reviewed_at": row.get("reviewed_at").isoformat() if row.get("reviewed_at") else None,
    }


def bounded_candidate_limit(value: Any, maximum: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 10
    return max(1, min(parsed, maximum))


def shared_semantic_labels(source: dict[str, Any], target: dict[str, Any]) -> list[str]:
    return sorted(
        label
        for label in set(source.get("labels") or []) & set(target.get("labels") or [])
        if label and label not in STRUCTURAL_LABELS and not label.startswith("source_")
    )


def parse_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


class InvalidEmbeddingBackfillScope(ValueError):
    def __init__(self, reason: str, field: str, value: str):
        super().__init__(reason)
        self.reason = reason
        self.field = field
        self.value = value


def embedding_backfill_node_query(
    *,
    label: str | None = None,
    document_id: str | None = None,
    force: bool = False,
    after_node_id: str | None = None,
) -> dict[str, Any]:
    document_object_id = parse_object_id(document_id) if document_id else None
    if document_id and document_object_id is None:
        raise InvalidEmbeddingBackfillScope("invalid_document_id", "document_id", document_id)
    after_object_id = parse_object_id(after_node_id) if after_node_id else None
    if after_node_id and after_object_id is None:
        raise InvalidEmbeddingBackfillScope("invalid_after_node_id", "after_node_id", after_node_id)

    query: dict[str, Any] = {"status": {"$ne": "superseded"}}
    if after_object_id is not None:
        query["_id"] = {"$gt": after_object_id}
    if document_object_id is not None:
        query["document_id"] = document_object_id
    label_filter = str(label).strip() if label else None
    if label_filter:
        query["labels"] = label_filter
    if not force:
        query["embedding.vector"] = {"$exists": False}
    return query


def count_backfill_embedding_candidates(
    db: Database,
    *,
    label: str | None = None,
    document_id: str | None = None,
    force: bool = False,
) -> int:
    return db.nodes.count_documents(
        embedding_backfill_node_query(
            label=label,
            document_id=document_id,
            force=force,
        )
    )


def normalized_relation_type(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().split())


def bounded_edge_score(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def summarize_node_text(text: str, limit: int = 500) -> str:
    return " ".join(text.split())[:limit]


def backfill_node_embeddings(
    db: Database,
    embedder: Any,
    *,
    limit: int = 100,
    label: str | None = None,
    document_id: str | None = None,
    force: bool = False,
    after_node_id: str | None = None,
    max_errors: int = 5,
) -> dict[str, Any]:
    bounded_limit = bounded_candidate_limit(limit, maximum=1000)
    label_filter = str(label).strip() if label else None
    try:
        query = embedding_backfill_node_query(
            label=label_filter,
            document_id=document_id,
            force=force,
            after_node_id=after_node_id,
        )
    except InvalidEmbeddingBackfillScope as error:
        result = {"ok": False, "reason": error.reason, error.field: error.value}
        return {**result, "activity_log": embedding_backfill_activity_log(result)}

    matched = 0
    scanned = 0
    updated = 0
    skipped = 0
    error_count = 0
    errors: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    last_dimensions = getattr(embedder, "dimensions", None)
    last_node_id = None

    try:
        for node in db.nodes.find(query).sort("_id", 1).limit(bounded_limit):
            scanned += 1
            matched += 1
            last_node_id = str(node.get("_id"))
            existing_embedding = node.get("embedding") or {}
            if not force and existing_embedding.get("vector"):
                last_dimensions = existing_embedding.get("dimensions") or last_dimensions
                skipped += 1
                continue
            try:
                embedding = embedder.embed(node.get("text") or "")
            except Exception as error:
                skipped += 1
                error_count += 1
                if len(errors) < max_errors:
                    errors.append(
                        {
                            "node_id": str(node.get("_id")),
                            "title": node.get("title"),
                            "error_type": error.__class__.__name__,
                            "error": str(error),
                        }
                    )
                continue
            db.nodes.update_one(
                {"_id": node["_id"]},
                {"$set": {"embedding": embedding, "updated_at": now}},
            )
            updated += 1
            last_dimensions = embedding.get("dimensions")
    finally:
        close_embedder = getattr(embedder, "close", None)
        if callable(close_embedder):
            close_embedder()

    all_attempted_updates_failed = matched > 0 and updated == 0 and error_count == matched
    result = {
        "ok": not all_attempted_updates_failed,
        **({"reason": "all_embedding_updates_failed"} if all_attempted_updates_failed else {}),
        "scanned_count": scanned,
        "matched_count": matched,
        "updated_count": updated,
        "skipped_count": skipped,
        "error_count": error_count,
        "error_sample_limit": max_errors,
        "errors": errors,
        "last_node_id": last_node_id,
        "adapter": getattr(embedder, "name", None),
        "model": getattr(embedder, "model", None),
        "dimensions": last_dimensions,
        "limit": bounded_limit,
        "filters": {
            "label": label_filter,
            "document_id": str(query.get("document_id")) if query.get("document_id") else None,
            "after_node_id": str((query.get("_id") or {}).get("$gt")) if query.get("_id") else None,
            "force": force,
            "status": "active",
            "missing_embedding_only": not force,
        },
    }
    return {**result, "activity_log": embedding_backfill_activity_log(result)}


def embedding_backfill_activity_log(result: dict[str, Any]) -> str:
    lines = ["Text Similarity Profile Backfill Activity Log"]
    if not result.get("ok"):
        lines.append(f"- Status: needs attention ({result.get('reason') or 'unknown reason'}).")
    else:
        lines.append("- Status: batch completed.")
    adapter = result.get("adapter")
    model = result.get("model")
    dimensions = result.get("dimensions")
    if adapter or model or dimensions:
        lines.append(
            f"- Profile adapter: {adapter or 'unknown adapter'} / "
            f"{model or 'unknown model'} ({dimensions or 'unknown'} dimensions)."
        )
    if "updated_count" in result:
        lines.append(
            f"- Repository action: {result.get('updated_count', 0)} node(s) given text similarity profiles, "
            f"{result.get('skipped_count', 0)} skipped, {result.get('error_count', 0)} error(s)."
        )
    filters = result.get("filters") or {}
    if filters:
        scope_parts = [
            f"limit {result.get('limit')}",
            f"label {filters.get('label') or 'any'}",
            f"document {filters.get('document_id') or 'any'}",
            f"after node {filters.get('after_node_id') or 'start'}",
            "force replace" if filters.get("force") else "missing profiles only",
        ]
        lines.append(f"- Scope: {', '.join(scope_parts)}.")
    if result.get("last_node_id"):
        lines.append(f"- Continuation point: next batch continues after node {result['last_node_id']}.")
        lines.append(
            "- Interruption behavior: node writes are saved one at a time; the job cursor is saved after a "
            "completed batch. If a run stops mid-batch, requeue the job. Missing-profile jobs skip profiles "
            "already written during the replay; forced jobs may rebuild the interrupted batch."
        )
    errors = result.get("errors") or []
    if errors:
        lines.append("- Sample errors:")
        for error in errors:
            title = error.get("title") or error.get("node_id") or "unknown node"
            lines.append(
                f"  - {title}: {error.get('error_type') or 'Error'} - {error.get('error') or ''}"
            )
    if result.get("reason") == "invalid_document_id":
        lines.append(f"- Correction: provide a valid Mongo document ObjectId. Received {result.get('document_id')}.")
    if result.get("reason") == "invalid_after_node_id":
        lines.append(f"- Correction: provide a valid Mongo node ObjectId. Received {result.get('after_node_id')}.")
    return "\n".join(lines)


def backfill_schema_metadata(db: Database) -> dict[str, int]:
    document_result = db.documents.update_many(
        {"schema_version": {"$exists": False}},
        [{"$set": {"schema_version": SCHEMA_VERSION, "updated_at": "$created_at"}}],
    )
    tree_result = db.trees.update_many(
        {"schema_version": {"$exists": False}},
        [
            {
                "$set": {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "source_document",
                    "updated_at": "$created_at",
                }
            }
        ],
    )

    nodes_updated = 0
    for node in db.nodes.find({"schema_version": {"$exists": False}}):
        source_path = node.get("provenance", {}).get("source_path")
        document = db.documents.find_one({"_id": node["document_id"]})
        source = document.get("source", {}) if document else {}
        endorsement_label = (
            node.get("endorsement_label")
            or node.get("provenance", {}).get("endorsement")
            or DEFAULT_ENDORSEMENT_LABEL
        )
        db.nodes.update_one(
            {"_id": node["_id"]},
            {
                "$set": {
                    "schema_version": SCHEMA_VERSION,
                    "endorsement_label": endorsement_label,
                    "provenance": {
                        "source_path": source_path or source.get("path"),
                        "source_checksum_sha256": source.get("checksum_sha256"),
                        "archive_path": source.get("archive_path"),
                        "endorsement_label": endorsement_label,
                        "adapter": node.get("metadata", {}).get("adapter", "mock"),
                    },
                    "updated_at": node.get("created_at"),
                },
            },
        )
        nodes_updated += 1

    node_default_result = db.nodes.update_many(
        {
            "$or": [
                {"summary": {"$exists": False}},
                {"relations": {"$exists": False}},
                {"proximity": {"$exists": False}},
                {"usage_score": {"$exists": False}},
                {"continuity_critical": {"$exists": False}},
            ]
        },
        [
            {
                "$set": {
                    "summary": {"$ifNull": ["$summary", ""]},
                    "relations": {"$ifNull": ["$relations", []]},
                    "proximity": {"$ifNull": ["$proximity", {}]},
                    "usage_score": {"$ifNull": ["$usage_score", 0]},
                    "continuity_critical": {"$ifNull": ["$continuity_critical", False]},
                }
            }
        ],
    )

    return {
        "documents": document_result.modified_count,
        "trees": tree_result.modified_count,
        "nodes": nodes_updated,
        "nodes_with_graph_defaults": node_default_result.modified_count,
    }


def label_definitions(db: Database) -> list[dict]:
    return list(db.label_definitions.find({}, {"_id": 0}).sort([("scope", 1), ("key", 1)]))


def document_tree(db: Database, document_id: str) -> list[dict]:
    from bson import ObjectId

    object_id = ObjectId(document_id)
    nodes = list(
        db.nodes.find(
            {"document_id": object_id, "status": {"$ne": "superseded"}},
            {
                "_id": 1,
                "parent_id": 1,
                "node_key": 1,
                "parent_key": 1,
                "order": 1,
                "title": 1,
                "labels": 1,
                "endorsement_label": 1,
            },
        ).sort("order", 1)
    )
    return [
        {
            "node_id": str(node["_id"]),
            "parent_id": str(node["parent_id"]) if node.get("parent_id") else None,
            "node_key": node.get("node_key"),
            "parent_key": node.get("parent_key"),
            "order": node["order"],
            "title": node["title"],
            "labels": node.get("labels", []),
            "endorsement_label": node.get("endorsement_label"),
        }
        for node in nodes
    ]
