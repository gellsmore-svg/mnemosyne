import pytest

from tirzah.retrieval import deep
from tirzah.retrieval.deep import (
    PrimitiveError,
    allowed_primitive_specs,
    run_primitive,
    validate_primitive_call,
)


def test_menu_lists_primitives_without_handlers() -> None:
    specs = {s["name"]: s for s in allowed_primitive_specs()}
    assert set(specs) == {"keyword_search", "hybrid_search", "adjacent_context", "graph_traverse"}
    assert "handler" not in specs["keyword_search"]
    assert specs["keyword_search"]["args"]["query"]["required"] is True


def test_validate_unknown_primitive_and_missing_required() -> None:
    with pytest.raises(PrimitiveError, match="unknown primitive"):
        validate_primitive_call("nope", {})
    with pytest.raises(PrimitiveError, match="missing required argument 'query'"):
        validate_primitive_call("keyword_search", {})


def test_validate_applies_defaults_and_ignores_unknown_args() -> None:
    args = validate_primitive_call("keyword_search", {"query": "memory", "bogus": 1})
    assert args == {"query": "memory", "limit": 10}  # default limit, unknown dropped


def test_validate_coerces_and_bounds_ints() -> None:
    assert validate_primitive_call("keyword_search", {"query": "q", "limit": "5"})["limit"] == 5
    assert validate_primitive_call("keyword_search", {"query": "q", "limit": 1000})["limit"] == 50
    assert validate_primitive_call("keyword_search", {"query": "q", "limit": 0})["limit"] == 1
    with pytest.raises(PrimitiveError, match="must be int"):
        validate_primitive_call("keyword_search", {"query": "q", "limit": "lots"})


def test_validate_rejects_non_object_args() -> None:
    with pytest.raises(PrimitiveError, match="must be an object"):
        validate_primitive_call("keyword_search", ["query"])


def test_run_primitive_dispatches(monkeypatch) -> None:
    calls = {}

    def fake_search(*a, **k):
        calls["search"] = k
        return ["s"]

    def fake_node(*a, **k):
        calls["node"] = (a, k)
        return {"n": 1}

    def fake_graph(*a, **k):
        calls["graph"] = (a, k)
        return ["g"]

    monkeypatch.setattr(deep, "search_nodes", fake_search)
    monkeypatch.setattr(deep, "node_context", fake_node)
    monkeypatch.setattr(deep, "expand_graph_paths", fake_graph)

    embedding = {"vector": [1.0], "dimensions": 1, "model": "m"}

    # keyword_search -> search_nodes, no query embedding (lexical)
    assert run_primitive(None, "keyword_search", {"query": "memory"}) == ["s"]
    assert "query_embedding" not in calls["search"]

    # hybrid_search -> search_nodes, forwards the query embedding
    run_primitive(None, "hybrid_search", {"query": "memory"}, query_embedding=embedding)
    assert calls["search"]["query_embedding"] is embedding

    # adjacent_context -> node_context, graph_traverse -> expand_graph_paths
    run_primitive(None, "adjacent_context", {"node_id": "n1"})
    assert calls["node"][0] == (None, "n1") and calls["node"][1]["child_limit"] == 20
    run_primitive(None, "graph_traverse", {"node_id": "n1", "depth": 3})
    assert calls["graph"][1]["max_depth"] == 3
