"""Tirzah MCP handlers — the memory-search seam for coding agents."""

from __future__ import annotations

from tirzah import mcp_handlers


def test_build_handlers_exposes_search_memory() -> None:
    handlers = mcp_handlers.build_handlers(db=object())
    assert "tirzah.search_memory" in handlers
    assert "search_memory" in handlers  # short alias too


def test_search_memory_compacts_nodes(monkeypatch) -> None:
    monkeypatch.setattr(
        "tirzah.retrieval.queries.search_nodes",
        lambda _db, query=None, limit=10: [
            {"node_id": "n1", "labels": ["substrate"], "content": "x" * 500, "document_id": "d1"},
            {"_id": "n2", "text": "short note"},
        ],
    )
    handler = mcp_handlers.build_handlers(db=object())["search_memory"]
    out = handler(query="substrate", limit=5)
    assert out["count"] == 2 and out["query"] == "substrate"
    first = out["results"][0]
    assert first["node_id"] == "n1" and first["labels"] == ["substrate"]
    assert first["text"].endswith("…") and len(first["text"]) == 401  # truncated
    assert out["results"][1]["node_id"] == "n2"


def test_search_memory_requires_query() -> None:
    handler = mcp_handlers.build_handlers(db=object())["search_memory"]
    assert "error" in handler(query="   ")


def test_search_memory_reports_missing_db(monkeypatch) -> None:
    # No reachable default DB → clean error, no raise (forced for determinism;
    # this env may otherwise have a live Mongo).
    monkeypatch.setattr(mcp_handlers, "_default_db", lambda: None)
    handler = mcp_handlers.build_handlers()["search_memory"]
    out = handler(query="anything")
    assert "error" in out and "unavailable" in out["error"]


def test_search_memory_surfaces_search_error(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("mongo down")

    monkeypatch.setattr("tirzah.retrieval.queries.search_nodes", boom)
    handler = mcp_handlers.build_handlers(db=object())["search_memory"]
    out = handler(query="x")
    assert "error" in out and "search failed" in out["error"]
