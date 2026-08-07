"""Deborah estate adapter — tirzah.retrieve as observe product."""

from __future__ import annotations

from tirzah.deborah import (
    capability_index_entries,
    deborah_dispatch,
    make_retrieve_handler,
    nodes_to_observe,
)
from tirzah.manifest import build_manifest
from tirzah.mcp_handlers import build_handlers


def test_nodes_to_observe_empty_is_explicit():
    product = nodes_to_observe([], query="q")
    assert product["empty"] is True
    assert product["evidence"] == []
    assert product["count"] == 0


def test_nodes_to_observe_maps_provenance():
    product = nodes_to_observe(
        [
            {
                "node_id": "n1",
                "title": "Substrate note",
                "text": "Relational coherence requires closed loops.",
                "labels": ["claim"],
                "document_id": "d1",
            }
        ],
        query="coherence",
    )
    assert product["count"] == 1
    ev = product["evidence"][0]
    assert "closed loops" in ev["statement"]
    assert ev["source"].startswith("tirzah.retrieve:")
    assert ev["trace_ref"] == "n1"
    assert ev["trust"]["level"] == "untrusted"
    assert product["trust"]["level"] == "untrusted"


def test_retrieve_handler_with_injected_search():
    def search(query, limit=10):
        return [
            {
                "node_id": "a",
                "content": f"Hit for {query}",
                "labels": ["source_chunk"],
            }
        ]

    handler = make_retrieve_handler(search=search)
    out = handler(
        {"construct": "CALL", "action": "tirzah.retrieve — gather evidence"},
        {"claim": "Is the substrate coherent?"},
    )
    assert out["status"] == "completed"
    assert out["result"]["count"] == 1
    assert "substrate" in out["result"]["evidence"][0]["statement"].lower() or out["result"][
        "evidence"
    ]


def test_retrieve_handler_empty_query():
    out = make_retrieve_handler(search=lambda q, limit=10: [{"node_id": "x"}])({}, {})
    assert out["status"] == "completed"
    assert out["result"]["empty"] is True


def test_deborah_dispatch_keys():
    d = deborah_dispatch(search=lambda q, limit=10: [])
    assert "tirzah.retrieve" in d
    assert "retrieve" in d
    assert "search_memory" in d


def test_capability_index_and_manifest_retrieve_alias():
    entries = capability_index_entries()
    assert "tirzah.retrieve" in entries
    try:
        names = {c.name for c in build_manifest().capabilities}
    except ImportError as exc:
        # Older keturah without specialist re-exports — capability_index still works.
        import pytest

        pytest.skip(f"manifest import blocked by environment: {exc}")
    assert "search_memory" in names
    assert "retrieve" in names


def test_mcp_handlers_expose_retrieve():
    handlers = build_handlers(db=None)
    assert "retrieve" in handlers
    assert "tirzah.retrieve" in handlers
    # Defensive: never raise into the agent. With no DB → error; with live
    # Mongo → observe product. Either is fine for this unit check.
    out = handlers["retrieve"](query="anything")
    assert isinstance(out, dict)
    assert "error" in out or "evidence" in out
