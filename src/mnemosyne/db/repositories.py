from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from mnemosyne.models.ingestion import (
    DEFAULT_ENDORSEMENT_LABEL,
    SCHEMA_VERSION,
    DocumentRecord,
    IngestionResult,
    NodeRecord,
    Provenance,
    TreeRecord,
)


class DuplicateSourceError(Exception):
    def __init__(self, checksum: str, existing_document_id: object) -> None:
        super().__init__(f"Duplicate source checksum: {checksum}")
        self.checksum = checksum
        self.existing_document_id = existing_document_id


def find_duplicate_by_checksum(db: Database, checksum: str) -> dict | None:
    return db.documents.find_one({"source.checksum_sha256": checksum})


def commit_ingestion(db: Database, result: IngestionResult) -> dict[str, Any]:
    if result.source.checksum_sha256:
        existing = find_duplicate_by_checksum(db, result.source.checksum_sha256)
        if existing:
            raise DuplicateSourceError(result.source.checksum_sha256, existing["_id"])

    document = DocumentRecord(
        title=result.title,
        summary=result.summary,
        source=result.source,
        created_at=result.created_at,
        updated_at=result.created_at,
    ).model_dump()
    document_id = db.documents.insert_one(document).inserted_id

    try:
        inserted = insert_tree_nodes(db, document_id, result)
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


def rebuild_document(db: Database, document_id: str, result: IngestionResult) -> dict[str, Any]:
    object_id = ObjectId(document_id)
    existing = db.documents.find_one({"_id": object_id})
    if not existing:
        raise ValueError(f"Document not found: {document_id}")

    previous_trees = list(db.trees.find({"document_id": object_id}))
    previous_nodes = list(db.nodes.find({"document_id": object_id}))
    previous_edges = list_graph_edges_for_document(db, object_id)
    delete_graph_edges_for_document(db, object_id)
    db.nodes.delete_many({"document_id": object_id})
    db.trees.delete_many({"document_id": object_id})
    try:
        db.documents.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "title": result.title,
                    "summary": result.summary,
                    "source": result.source.model_dump(),
                    "updated_at": result.created_at,
                }
            },
        )
        inserted = insert_tree_nodes(db, object_id, result)
    except Exception:
        delete_graph_edges_for_document(db, object_id)
        db.nodes.delete_many({"document_id": object_id})
        db.trees.delete_many({"document_id": object_id})
        db.documents.replace_one({"_id": object_id}, existing)
        if previous_trees:
            db.trees.insert_many(previous_trees)
        if previous_nodes:
            db.nodes.insert_many(previous_nodes)
        if previous_edges and hasattr(db, "graph_edges"):
            db.graph_edges.insert_many(previous_edges)
        raise
    return {
        "document_id": str(object_id),
        "replaced": True,
        **inserted,
    }


def insert_tree_nodes(db: Database, document_id: object, result: IngestionResult) -> dict[str, Any]:
    tree = TreeRecord(
        document_id=document_id,
        label=result.tree_label,
        created_at=result.created_at,
        updated_at=result.created_at,
    )
    tree_doc = tree.model_dump()
    tree_id = db.trees.insert_one(tree_doc).inserted_id

    key_to_id: dict[str, object] = {}
    node_ids = []
    for order, node in enumerate(result.nodes):
        endorsement_label = node.endorsement_label or DEFAULT_ENDORSEMENT_LABEL
        parent_id = key_to_id.get(node.parent_key) if node.parent_key else None
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
            provenance=Provenance(
                source_path=result.source.path,
                source_checksum_sha256=result.source.checksum_sha256,
                archive_path=result.source.archive_path,
                endorsement_label=endorsement_label,
                adapter=result.adapter,
            ),
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


def summarize_node_text(text: str, limit: int = 500) -> str:
    return " ".join(text.split())[:limit]


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
            {"document_id": object_id},
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
