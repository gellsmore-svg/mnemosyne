from __future__ import annotations

from typing import Any

from pymongo.errors import CollectionInvalid


REQUIRED_COLLECTIONS = (
    "active_documents",
    "agent_identities",
    "conversation_domains",
    "documents",
    "embedding_backfill_jobs",
    "exchanges",
    "governance_policies",
    "graph_edges",
    "label_definitions",
    "nodes",
    "output_ingestion_queue",
    "process_objects",
    "process_runs",
    "project_domains",
    "queue",
    "retrieval_traces",
    "semantic_edge_candidates",
    "sessions",
    "tirzah_meta",
    "trees",
    "trust_weighting_profiles",
)


def ensure_required_collections(db: Any) -> None:
    existing = set(db.list_collection_names())
    for name in REQUIRED_COLLECTIONS:
        if name in existing:
            continue
        try:
            db.create_collection(name)
        except CollectionInvalid:
            pass


def collection_available(db: Any, name: str) -> bool:
    list_collection_names = getattr(db, "list_collection_names", None)
    if callable(list_collection_names):
        try:
            return name in set(list_collection_names())
        except Exception:
            return False
    return hasattr(db, name)
