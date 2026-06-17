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


from tirzah.retrieval.deep import DeepRetrievalSession


def _n(i):
    return {"node_id": i}


def test_session_filter_new_dedups_across_rounds() -> None:
    s = DeepRetrievalSession(max_iterations=4)
    first = s.filter_new([_n("a"), _n("b"), _n("a")])  # in-batch dup dropped
    assert [n["node_id"] for n in first] == ["a", "b"]
    second = s.filter_new([_n("b"), _n("c")])  # 'b' already seen
    assert [n["node_id"] for n in second] == ["c"]
    assert s.exclusion_ids == {"a", "b", "c"}


def test_session_add_useful_dedups() -> None:
    s = DeepRetrievalSession(max_iterations=4)
    s.add_useful([_n("a"), _n("b")])
    s.add_useful([_n("b"), _n("c")])
    assert [n["node_id"] for n in s.useful_chunks] == ["a", "b", "c"]


def test_session_stop_max_iterations() -> None:
    s = DeepRetrievalSession(max_iterations=2)
    s.record_round(new_count=5, total_count=5, best_score=1.0)
    assert s.should_stop() == (False, None)
    s.record_round(new_count=5, total_count=5, best_score=2.0)
    assert s.should_stop() == (True, "max_iterations")


def test_session_stop_no_new_and_low_novelty() -> None:
    s = DeepRetrievalSession(max_iterations=9)
    s.record_round(new_count=0, total_count=8)
    assert s.should_stop() == (True, "no_new_candidates")
    s2 = DeepRetrievalSession(max_iterations=9, min_novelty=0.5)
    s2.record_round(new_count=1, total_count=10)  # novelty 0.1 < 0.5
    assert s2.should_stop() == (True, "low_novelty")


def test_session_stop_diminishing_returns_and_continue() -> None:
    s = DeepRetrievalSession(max_iterations=9)
    s.record_round(new_count=5, total_count=5, best_score=0.9)
    s.record_round(new_count=4, total_count=8, best_score=0.9)
    s.record_round(new_count=3, total_count=9, best_score=0.9)  # no score gain over window
    assert s.should_stop() == (True, "diminishing_returns")

    s2 = DeepRetrievalSession(max_iterations=9)
    s2.record_round(new_count=5, total_count=5, best_score=0.5)
    s2.record_round(new_count=4, total_count=8, best_score=0.7)
    s2.record_round(new_count=3, total_count=9, best_score=0.9)  # still improving + novel
    assert s2.should_stop() == (False, None)


from types import SimpleNamespace

from tirzah.retrieval.deep import run_deep_retrieval


def _cfg(max_it=3, shortlist=12, page=5):
    return SimpleNamespace(
        retrieval=SimpleNamespace(
            deep_max_iterations=max_it, deep_shortlist_size=shortlist, deep_page_size=page
        )
    )


def _planner(seq):
    it = iter(seq)
    return lambda ctx: next(it, {"action": "stop"})


def test_deep_loop_happy_path(monkeypatch) -> None:
    counter = {"n": 0}

    def fake_run(db, name, args, *, query_embedding=None, identity=None):
        counter["n"] += 1
        base = counter["n"] * 10
        return [{"node_id": f"n{base}"}, {"node_id": f"n{base + 1}"}]

    monkeypatch.setattr(deep, "run_primitive", fake_run)
    planner = _planner(
        [
            {"primitive": "keyword_search", "args": {"query": "q"}},
            {"primitive": "keyword_search", "args": {"query": "q2"}},
            {"action": "stop"},
        ]
    )
    out = run_deep_retrieval(None, "q", config=_cfg(), planner=planner, triager=lambda q, page: page[:1])
    assert [c["node_id"] for c in out["useful_chunks"]] == ["n10", "n20"]
    assert [t["step"] for t in out["trace"]] == ["search", "search", "stop"]
    assert out["trace"][-1]["reason"] == "planner_stop"


def test_deep_loop_stops_at_max_iterations(monkeypatch) -> None:
    counter = {"n": 0}

    def fake_run(db, name, args, *, query_embedding=None, identity=None):
        counter["n"] += 1
        return [{"node_id": f"n{counter['n']}"}]  # new each round

    monkeypatch.setattr(deep, "run_primitive", fake_run)
    planner = lambda ctx: {"primitive": "keyword_search", "args": {"query": "q"}}
    out = run_deep_retrieval(None, "q", config=_cfg(max_it=2), planner=planner, triager=lambda q, p: [])
    assert out["trace"][-1]["reason"] == "max_iterations"
    assert sum(1 for t in out["trace"] if t["step"] == "search") == 2


def test_deep_loop_stops_on_no_new(monkeypatch) -> None:
    monkeypatch.setattr(deep, "run_primitive", lambda *a, **k: [{"node_id": "same"}])
    planner = lambda ctx: {"primitive": "keyword_search", "args": {"query": "q"}}
    out = run_deep_retrieval(None, "q", config=_cfg(max_it=9), planner=planner, triager=lambda q, p: p)
    assert out["trace"][-1]["reason"] == "no_new_candidates"


def test_deep_loop_bounded_invalid_plans() -> None:
    # real run_primitive raises PrimitiveError for an unknown primitive
    planner = lambda ctx: {"primitive": "bogus", "args": {}}
    out = run_deep_retrieval(None, "q", config=_cfg(max_it=2), planner=planner, triager=lambda q, p: p)
    assert out["trace"][-1]["reason"] == "repeated_invalid_plans"
    assert out["useful_chunks"] == []


import json as _json

from tirzah.retrieval.deep import (
    make_planner,
    make_triager,
    parse_deep_decision,
    parse_deep_triage,
)


class _ScriptedAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.i = 0

    def answer(self, prompt):
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return {"answer": r}


def test_parse_deep_decision_variants() -> None:
    assert parse_deep_decision('{"action": "stop"}') == {"action": "stop"}
    assert parse_deep_decision('```json\n{"primitive": "keyword_search", "args": {"query": "q"}}\n```') == {
        "primitive": "keyword_search",
        "args": {"query": "q"},
    }
    # garbage / no primitive → routed to bounded invalid-plan repair
    assert parse_deep_decision("not json")["primitive"] == "__unparseable__"
    assert parse_deep_decision('{"rationale": "hmm"}')["primitive"] == "__unparseable__"


def test_parse_deep_triage_keeps_listed_ids() -> None:
    page = [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}]
    assert [n["node_id"] for n in parse_deep_triage('["a", "c"]', page)] == ["a", "c"]
    assert parse_deep_triage("nope", page) == []
    assert parse_deep_triage('{"keep": "a"}', page) == []  # not a list


def test_make_planner_and_triager() -> None:
    planner = make_planner(_ScriptedAdapter([_json.dumps({"primitive": "keyword_search", "args": {"query": "q"}})]))
    assert planner({"query": "q", "primitives": []}) == {"primitive": "keyword_search", "args": {"query": "q"}}

    triager = make_triager(_ScriptedAdapter(['["b"]']))
    page = [{"node_id": "a"}, {"node_id": "b"}]
    assert [n["node_id"] for n in triager("q", page)] == ["b"]
    assert make_triager(_ScriptedAdapter(["[]"]))("q", []) == []  # empty page → no model call


def test_deep_loop_with_real_planner_triager(monkeypatch) -> None:
    monkeypatch.setattr(
        deep,
        "run_primitive",
        lambda *a, **k: [{"node_id": "n10", "text_preview": "hi"}, {"node_id": "n11", "text_preview": "yo"}],
    )
    adapter = _ScriptedAdapter(
        [
            _json.dumps({"primitive": "keyword_search", "args": {"query": "q"}}),
            _json.dumps(["n10"]),
            _json.dumps({"action": "stop"}),
        ]
    )
    out = run_deep_retrieval(
        None, "q", config=_cfg(max_it=5), planner=make_planner(adapter), triager=make_triager(adapter)
    )
    assert [c["node_id"] for c in out["useful_chunks"]] == ["n10"]
    assert out["trace"][-1]["reason"] == "planner_stop"


from tirzah.retrieval.deep import run_deep_answer, synthesize_answer


class _CapturingAdapter:
    def __init__(self, answer="ans"):
        self.prompts = []
        self._answer = answer

    def answer(self, prompt):
        self.prompts.append(prompt["prompt_text"])
        return {"answer": self._answer}


def test_synthesize_answer_with_and_without_chunks() -> None:
    a = _CapturingAdapter("written")
    out = synthesize_answer("what?", [{"node_id": "n1", "title": "T", "text": "body"}], a)
    assert out == "written"
    assert "[n1]" in a.prompts[0] and "body" in a.prompts[0] and "what?" in a.prompts[0]

    a2 = _CapturingAdapter("none")
    assert synthesize_answer("q", [], a2) == "none"
    assert "no relevant information" in a2.prompts[0]


def test_run_deep_answer_end_to_end(monkeypatch) -> None:
    monkeypatch.setattr(
        deep, "run_primitive", lambda *a, **k: [{"node_id": "n10", "text_preview": "hi"}, {"node_id": "n11"}]
    )
    adapter = _ScriptedAdapter(
        [
            _json.dumps({"primitive": "keyword_search", "args": {"query": "q"}}),
            _json.dumps(["n10"]),
            _json.dumps({"action": "stop"}),
            "Final answer.",
        ]
    )
    rc = SimpleNamespace(hybrid_search_enabled=False, embedding_adapter="mock")
    out = run_deep_answer(None, "q", config=_cfg(max_it=5), runtime_config=rc, adapter=adapter)
    assert out["answer"] == "Final answer."
    assert [c["node_id"] for c in out["useful_chunks"]] == ["n10"]
