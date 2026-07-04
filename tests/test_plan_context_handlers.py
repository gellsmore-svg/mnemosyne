from tirzah.config import AppConfig, RuntimeConfig
from tirzah.planning.context_bundle import (
    append_tool_result,
    compact_context_bundle_summary,
    ensure_bundle,
    resolve_compile_node_id,
    resolve_focus_node_id,
    resolve_web_fetch_url,
)
from tirzah.planning.executor import build_default_handlers, interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep


def _plan(*steps: PlanStep) -> CairnPlan:
    return CairnPlan(
        plan_id="plan_ctx",
        revision=1,
        parent_revision=None,
        request="What is X?",
        trigger="initial_request",
        objective="What is X?",
        status="active",
        steps=list(steps),
    )


def test_context_bundle_helpers_resolve_node_and_url():
    artifacts: dict = {}
    bundle = ensure_bundle(artifacts)
    append_tool_result(
        bundle,
        tool="search_nodes",
        output={"matches": [{"node_id": "node-1", "title": "Hit"}]},
        arguments={"query": "X"},
    )
    append_tool_result(
        bundle,
        tool="web_search",
        output={"sources": [{"url": "https://example.com/doc", "title": "Doc"}]},
        arguments={"query": "X"},
    )
    assert resolve_compile_node_id(bundle, {}) == "node-1"
    assert resolve_compile_node_id(bundle, {"focus_node_id": "focus-9"}) == "focus-9"
    assert resolve_web_fetch_url(bundle) == "https://example.com/doc"


def test_granular_search_compile_synthesize_plan(monkeypatch):
    monkeypatch.setattr(
        "tirzah.sessions.interaction.execute_search_nodes_tool",
        lambda _db, query, **kwargs: (
            {"matches": [{"node_id": "n1", "title": "Target"}], "compiled_contexts": []},
            {"normalized_query": query},
        ),
    )
    monkeypatch.setattr(
        "tirzah.retrieval.queries.compile_context",
        lambda _db, node_id, **_kwargs: {
            "focus_node_id": node_id,
            "document": {"title": "Doc"},
            "records": [{"node_id": node_id, "role": "focus", "distance": 0, "text": "body"}],
        },
    )
    monkeypatch.setattr(
        "tirzah.sessions.answer_phases._begin_answer_request",
        lambda *_args, **_kwargs: (RuntimeConfig(answer_adapter="mock"), [], "run1"),
    )
    monkeypatch.setattr(
        "tirzah.sessions.interaction.build_agentic_answer_envelope",
        lambda **_kwargs: {
            "prompt_text": "prompt",
            "budget": {},
            "context_metadata": {
                "retrieval_status": "matched_context",
                "included": [{"node_id": "n1"}],
                "evidence_summary": {},
            },
        },
    )
    monkeypatch.setattr(
        "tirzah.sessions.interaction.inject_history_into_prompt",
        lambda prompt, _history: prompt,
    )
    monkeypatch.setattr(
        "tirzah.sessions.interaction.render_session_history_block",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        "tirzah.sessions.answer_phases.synthesize_from_context_bundle",
        lambda _db, _config, **kwargs: {
            "ok": True,
            "answer": "from granular bundle",
            "used_node_ids": ["n1"],
            "retrieval_status": "matched_context",
        },
    )

    plan = _plan(
        PlanStep(id="1", action="Search memory", construct="CALL", allowed_tools=["search_nodes"]),
        PlanStep(
            id="2",
            action="Compile focus context",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["compile_context"],
        ),
        PlanStep(
            id="3",
            action="Synthesize answer",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["answer_adapter"],
        ),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(),
        answer_kwargs={"session_id": "s1"},
    )
    result = interpret_plan(plan, query="What is X?", session_id="s1", handlers=handlers)
    assert result.ok
    assert result.primary_result["answer"] == "from granular bundle"
    bundle = result.context.artifacts["context_bundle"]
    assert [row["tool"] for row in bundle["tool_results"]] == ["search_nodes", "compile_context"]


def test_compile_context_without_node_id_is_blocked():
    plan = _plan(
        PlanStep(id="1", action="Compile", construct="CALL", allowed_tools=["compile_context"]),
    )
    handlers = build_default_handlers(db=object(), config=AppConfig(), answer_kwargs={})
    result = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert not result.ok
    assert result.plan.steps[0].status == "blocked"


def test_resolve_focus_node_id_prefers_compile_context():
    artifacts: dict = {}
    bundle = ensure_bundle(artifacts)
    append_tool_result(
        bundle,
        tool="search_nodes",
        output={"matches": [{"node_id": "search-hit"}]},
        arguments={},
    )
    append_tool_result(
        bundle,
        tool="compile_context",
        output={"focus_node_id": "compiled-focus", "document": {}, "records": []},
        arguments={},
    )
    assert resolve_focus_node_id(bundle, {}) == "compiled-focus"


def test_expand_proximity_handler_appends_to_bundle(monkeypatch):
    monkeypatch.setattr(
        "tirzah.sessions.interaction.execute_expand_proximity_tool",
        lambda _db, node_id, **_kwargs: {
            "matches": [{"node_id": "near-1", "title": "Neighbor"}],
            "compiled_contexts": [],
        },
    )
    plan = _plan(
        PlanStep(
            id="1",
            action="Expand graph",
            construct="CALL",
            allowed_tools=["expand_proximity"],
        ),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1"},
    )
    result = interpret_plan(plan, query="Q", session_id="s1", handlers=handlers)
    assert result.ok
    tools = [row["tool"] for row in result.context.artifacts["context_bundle"]["tool_results"]]
    assert tools == ["expand_proximity"]
    assert compact_context_bundle_summary(result.context.artifacts["context_bundle"]) == {
        "tool_count": 1,
        "tools": ["expand_proximity"],
        "ok_count": 1,
    }


def test_web_search_disabled_blocks_step():
    plan = _plan(
        PlanStep(id="1", action="Search web", construct="CALL", allowed_tools=["web_search"]),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(runtime=RuntimeConfig(web_research_enabled=False)),
        answer_kwargs={},
    )
    result = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert not result.ok
    assert result.plan.steps[0].status == "blocked"