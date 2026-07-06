from tirzah.config import AppConfig, RuntimeConfig
from tirzah.planning.constructs import (
    cascade_skip_dependents,
    error_signal_matches,
    evaluate_decision_branch,
    execute_decision_step,
    execute_error_step,
    execute_iterate_step,
    execute_merge_step,
    execute_parallel_step,
    execute_retry_step,
    is_owned_by_pending_parent,
    parse_error_handler,
    parse_max_rounds,
    suggest_plan_profile_hint,
)
from tirzah.planning.executor import build_default_handlers, interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep


def _plan(*steps: PlanStep) -> CairnPlan:
    return CairnPlan(
        plan_id="plan_iter",
        revision=1,
        parent_revision=None,
        request="Q",
        trigger="t",
        objective="Q",
        status="active",
        steps=list(steps),
    )


def test_parse_max_rounds_reads_iterate_bound():
    step = PlanStep(id="1", action="Gather until sufficient MAX: 2", construct="ITERATE")
    assert parse_max_rounds(step) == 2


def test_iterate_runs_body_until_has_matches():
    steps = [
        PlanStep(id="1", action="Loop", construct="ITERATE", success_criteria=["until:has_matches"]),
        PlanStep(id="2", action="Search", construct="CALL", depends_on=["1"], allowed_tools=["search_nodes"]),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls = {"count": 0}

    def run_step(body_step):
        calls["count"] += 1
        artifacts.setdefault("context_bundle", {"tool_results": []})
        if calls["count"] >= 1:
            artifacts["context_bundle"]["tool_results"].append(
                {"tool": "search_nodes", "ok": True, "output": {"matches": [{"node_id": "n1"}]}}
            )
        return {"status": "completed", "artifact": {"ok": True}}

    outcome = execute_iterate_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        trace=trace,
        run_step=run_step,
    )
    assert outcome["status"] == "completed"
    assert calls["count"] == 1
    assert "2" in completed
    assert any(row["step"] == "plan.iterate.round" for row in trace)


def test_iterate_reports_blocked_when_body_blocks():
    steps = [
        PlanStep(id="1", action="Loop", construct="ITERATE"),
        PlanStep(id="2", action="Blocked body", construct="CALL", depends_on=["1"]),
    ]
    outcome = execute_iterate_step(
        steps[0],
        steps=steps,
        completed=set(),
        artifacts={},
        trace=[],
        run_step=lambda _step: {"status": "blocked", "reason": "handler_down"},
    )
    assert outcome["status"] == "blocked"
    assert outcome["reason"] == "iterate_body_blocked"
    assert outcome["artifact"]["blocked_reason"] == "handler_down"


def test_decision_skips_unselected_branch():
    steps = [
        PlanStep(id="1", action="ON: web_research", construct="DECISION"),
        PlanStep(
            id="2a",
            action="Web",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["web_search"],
            success_criteria=["branch:web"],
        ),
        PlanStep(
            id="2b",
            action="Memory",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["search_nodes"],
            success_criteria=["branch:memory"],
        ),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    outcome = execute_decision_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        answer_kwargs={"web_research": False},
        config=AppConfig(runtime=RuntimeConfig(web_research_enabled=False)),
        trace=trace,
    )
    assert outcome["artifact"]["branch"] == "memory"
    assert steps[1].status == "skipped"
    assert steps[2].status == "pending"
    assert "2a" in completed and "2a" not in outcome["artifact"]["selected_steps"]


def test_parse_error_handler_reads_on_then_and_fallback():
    step = PlanStep(
        id="1",
        action="ON: handler_failed THEN: fallback",
        construct="ERROR",
        success_criteria=["fallback:3", "on:handler_failed"],
    )
    parsed = parse_error_handler(step)
    assert parsed["on"] == "handler_failed"
    assert parsed["then"] == "fallback"
    assert parsed["fallback_step_id"] == "3"
    assert error_signal_matches("handler_failed", "handler_failed")


def test_error_fallback_runs_recovery_step():
    steps = [
        PlanStep(
            id="1",
            action="ON: transient THEN: fallback",
            construct="ERROR",
            success_criteria=["fallback:3"],
        ),
        PlanStep(id="2", action="Risky", construct="CALL", depends_on=["1"]),
        PlanStep(id="3", action="Recover", construct="CALL", depends_on=["1"]),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls: list[str] = []

    def run_step(body_step):
        calls.append(body_step.id)
        if body_step.id == "2":
            return {"status": "blocked", "reason": "transient"}
        return {"status": "completed", "artifact": {"ok": True, "step": body_step.id}}

    outcome = execute_error_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        run_step=run_step,
        trace=trace,
    )
    assert outcome["status"] == "completed"
    assert outcome["artifact"]["fallback_step_id"] == "3"
    assert calls == ["2", "3"]
    assert steps[1].status == "skipped"
    assert steps[2].status == "completed"
    assert any(row["step"] == "plan.error.triggered" for row in trace)


def test_error_propagate_returns_blocked_when_then_propagate():
    steps = [
        PlanStep(id="1", action="ON: fatal THEN: propagate", construct="ERROR"),
        PlanStep(id="2", action="Risky", construct="CALL", depends_on=["1"]),
    ]
    outcome = execute_error_step(
        steps[0],
        steps=steps,
        completed=set(),
        artifacts={},
        run_step=lambda _step: {"status": "blocked", "reason": "fatal"},
        trace=[],
    )
    assert outcome["status"] == "blocked"
    assert outcome["reason"] == "fatal"


def test_interpret_plan_error_fallback_to_memory_search(monkeypatch):
    monkeypatch.setattr(
        "tirzah.sessions.interaction.execute_search_nodes_tool",
        lambda *_db, query, **kwargs: (
            {"matches": [{"node_id": "n1", "title": "Hit"}], "compiled_contexts": []},
            {},
        ),
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
            "answer": "fallback answer",
            "used_node_ids": ["n1"],
            "retrieval_status": "matched_context",
        },
    )

    plan = _plan(
        PlanStep(id="1", action="Interpret", construct="STEP"),
        PlanStep(
            id="2",
            action="ON: web_research_disabled THEN: fallback",
            construct="ERROR",
            depends_on=["1"],
            success_criteria=["fallback:3", "on:web_research_disabled"],
        ),
        PlanStep(
            id="2a",
            action="Web",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["web_search"],
        ),
        PlanStep(
            id="3",
            action="Search memory",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["search_nodes"],
        ),
        PlanStep(
            id="4",
            action="Answer",
            construct="CALL",
            depends_on=["3"],
            allowed_tools=["answer_adapter"],
        ),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(runtime=RuntimeConfig(web_research_enabled=False)),
        answer_kwargs={"session_id": "s1"},
        use_split_phases=True,
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers=handlers,
        config=AppConfig(runtime=RuntimeConfig(web_research_enabled=False)),
        answer_kwargs={"session_id": "s1"},
    )
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses["2"] == "completed"
    assert statuses["2a"] == "skipped"
    assert statuses["3"] == "completed"
    assert result.ok
    assert result.primary_result["answer"] == "fallback answer"
    assert any(row.get("step") == "plan.error.triggered" for row in result.context.trace)


def test_isolated_parallel_preserves_parent_context_bundle():
    steps = [
        PlanStep(id="1", action="Gather branches STATE: isolated", construct="PARALLEL"),
        PlanStep(id="1a", action="A", construct="CALL", depends_on=["1"]),
        PlanStep(id="1b", action="B", construct="CALL", depends_on=["1"]),
    ]
    artifacts = {"context_bundle": {"tool_results": [{"tool": "parent", "ok": True, "output": {}}]}}
    trace: list = []
    completed: set[str] = set()

    def branch_runner(body_step):
        artifacts["context_bundle"]["tool_results"].append(
            {"tool": body_step.id, "ok": True, "output": {"branch": body_step.id}, "arguments": {}}
        )
        artifacts[body_step.id] = {"ok": True, "tool": body_step.id}
        return {"status": "completed", "artifact": artifacts[body_step.id]}

    outcome = execute_parallel_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        branch_runner=branch_runner,
        trace=trace,
    )
    assert outcome["status"] == "completed"
    assert [row["tool"] for row in artifacts["context_bundle"]["tool_results"]] == ["parent"]
    isolated_a = artifacts["parallel:1"]["branches"]["1a"]["context_bundle"]["tool_results"]
    isolated_b = artifacts["parallel:1"]["branches"]["1b"]["context_bundle"]["tool_results"]
    assert len(isolated_a) == 1
    assert len(isolated_b) == 1


def test_merge_isolated_parallel_context_bundles():
    steps = [
        PlanStep(id="1", action="Parallel STATE: isolated", construct="PARALLEL"),
        PlanStep(id="1a", action="A", construct="CALL", depends_on=["1"]),
        PlanStep(
            id="2",
            action="Merge",
            construct="MERGE",
            depends_on=["1a"],
            success_criteria=["merge:context_bundle"],
        ),
    ]
    artifacts = {
        "parallel:1": {
            "state": "isolated",
            "branch_ids": ["1a"],
            "branches": {
                "1a": {
                    "context_bundle": {
                        "tool_results": [
                            {"tool": "search_nodes", "ok": True, "output": {"matches": []}, "arguments": {}},
                        ],
                    },
                },
            },
        },
    }
    execute_merge_step(steps[2], steps=steps, artifacts=artifacts, trace=[])
    assert artifacts["context_bundle"]["tool_results"][0]["tool"] == "search_nodes"


def test_retry_reruns_blocked_body_until_success():
    steps = [
        PlanStep(id="1", action="Retry search MAX: 3", construct="RETRY"),
        PlanStep(id="2", action="Search", construct="CALL", depends_on=["1"], allowed_tools=["search_nodes"]),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls = {"count": 0}

    def run_step(body_step):
        calls["count"] += 1
        if calls["count"] < 2:
            return {"status": "blocked", "reason": "transient"}
        return {"status": "completed", "artifact": {"ok": True}}

    outcome = execute_retry_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        run_step=run_step,
        trace=trace,
    )
    assert outcome["status"] == "completed"
    assert outcome["artifact"]["attempts"] == 2
    assert calls["count"] == 2
    assert any(row["step"] == "plan.retry.attempt" for row in trace)


def test_parallel_runs_all_branch_subtrees():
    steps = [
        PlanStep(id="1", action="Gather branches STATE: shared", construct="PARALLEL"),
        PlanStep(
            id="1a",
            action="Search",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["search_nodes"],
        ),
        PlanStep(
            id="1b",
            action="Compile",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["compile_context"],
        ),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls: list[str] = []

    def branch_runner(body_step):
        calls.append(body_step.id)
        artifacts[body_step.id] = {
            "ok": True,
            "tool": body_step.allowed_tools[0],
            "tool_result": {
                "tool": body_step.allowed_tools[0],
                "ok": True,
                "output": {"matches": [{"node_id": body_step.id}]},
                "arguments": {},
            },
        }
        return {"status": "completed", "artifact": artifacts[body_step.id]}

    outcome = execute_parallel_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        branch_runner=branch_runner,
        trace=trace,
    )
    assert outcome["status"] == "completed"
    assert set(calls) == {"1a", "1b"}
    assert artifacts["parallel:1"]["branch_ids"] == ["1a", "1b"]
    assert any(row["step"] == "plan.parallel.completed" for row in trace)


def test_merge_collects_parallel_branch_tool_results():
    steps = [
        PlanStep(id="1", action="Parallel", construct="PARALLEL"),
        PlanStep(id="1a", action="A", construct="CALL", depends_on=["1"]),
        PlanStep(id="1b", action="B", construct="CALL", depends_on=["1"]),
        PlanStep(
            id="2",
            action="Merge gathered context",
            construct="MERGE",
            depends_on=["1a", "1b"],
            success_criteria=["merge:context_bundle"],
        ),
    ]
    artifacts = {
        "parallel:1": {"branch_ids": ["1a", "1b"], "branches": {}},
        "1a": {
            "ok": True,
            "tool": "search_nodes",
            "tool_result": {
                "tool": "search_nodes",
                "ok": True,
                "output": {"matches": [{"node_id": "n1"}]},
                "arguments": {"query": "q"},
            },
        },
        "1b": {
            "ok": True,
            "tool": "compile_context",
            "tool_result": {
                "tool": "compile_context",
                "ok": True,
                "output": {"focus_node_id": "n1"},
                "arguments": {"node_id": "n1"},
            },
        },
    }
    trace: list = []
    outcome = execute_merge_step(steps[3], steps=steps, artifacts=artifacts, trace=trace)
    assert outcome["status"] == "completed"
    tools = [row["tool"] for row in artifacts["context_bundle"]["tool_results"]]
    assert tools == ["search_nodes", "compile_context"]
    assert any(row["step"] == "plan.parallel.merged" for row in trace)


def test_interpret_plan_parallel_merge_and_answer(monkeypatch):
    monkeypatch.setattr(
        "tirzah.sessions.interaction.execute_search_nodes_tool",
        lambda *_db, query, **kwargs: (
            {"matches": [{"node_id": "n1", "title": "Hit"}], "compiled_contexts": []},
            {},
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
            "answer": "parallel merged",
            "used_node_ids": ["n1"],
            "retrieval_status": "matched_context",
        },
    )

    plan = _plan(
        PlanStep(id="1", action="Interpret", construct="STEP"),
        PlanStep(id="2", action="Parallel gather STATE: shared", construct="PARALLEL", depends_on=["1"]),
        PlanStep(
            id="2a",
            action="Search",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["search_nodes"],
        ),
        PlanStep(
            id="2b",
            action="Compile",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["compile_context"],
        ),
        PlanStep(
            id="3",
            action="Merge",
            construct="MERGE",
            depends_on=["2a", "2b"],
            success_criteria=["merge:context_bundle"],
        ),
        PlanStep(
            id="4",
            action="Answer",
            construct="CALL",
            depends_on=["3"],
            allowed_tools=["answer_adapter"],
        ),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
        use_split_phases=True,
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers=handlers,
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
    )
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses["2"] == "completed"
    assert statuses["2a"] == "completed"
    assert statuses["2b"] == "completed"
    assert statuses["3"] == "completed"
    assert result.ok
    assert result.primary_result["answer"] == "parallel merged"
    assert any(row.get("step") == "plan.parallel.merged" for row in result.context.trace)


def test_owned_by_pending_parent_hides_parallel_branches():
    steps = [
        PlanStep(id="1", action="Parallel", construct="PARALLEL"),
        PlanStep(id="2", action="Branch", construct="CALL", depends_on=["1"]),
    ]
    assert is_owned_by_pending_parent(steps[1], steps, set()) is True
    assert is_owned_by_pending_parent(steps[1], steps, {"1"}) is False


def test_interpret_plan_inline_decision_branches_inside_iterate(monkeypatch):
    tool_order: list[str] = []

    def fake_search(*_args, **_kwargs):
        tool_order.append("search_nodes")
        return {"matches": [{"node_id": "n1", "title": "Hit"}], "compiled_contexts": []}, {}

    monkeypatch.setattr("tirzah.sessions.interaction.execute_search_nodes_tool", fake_search)
    monkeypatch.setattr(
        "tirzah.retrieval.queries.compile_context",
        lambda _db, node_id, **_kwargs: (
            tool_order.append("compile_context") or {
                "focus_node_id": node_id,
                "document": {"title": "Doc"},
                "records": [{"node_id": node_id, "role": "focus", "distance": 0, "text": "body"}],
            }
        ),
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
            "answer": "inline iterate answer",
            "used_node_ids": ["n1"],
            "retrieval_status": "matched_context",
        },
    )

    plan = _plan(
        PlanStep(
            id="1",
            action="Gather MAX: 2",
            construct="ITERATE",
            success_criteria=["until:context_ready"],
        ),
        PlanStep(
            id="2",
            action="Search",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["search_nodes"],
        ),
        PlanStep(
            id="3",
            action="ON: context_depth",
            construct="DECISION",
            depends_on=["1", "2"],
        ),
        PlanStep(
            id="3a",
            action="Compile",
            construct="CALL",
            depends_on=["3"],
            allowed_tools=["compile_context"],
            success_criteria=["branch:compile"],
        ),
        PlanStep(
            id="3b",
            action="Expand",
            construct="CALL",
            depends_on=["3"],
            allowed_tools=["expand_proximity"],
            success_criteria=["branch:expand"],
        ),
        PlanStep(
            id="4",
            action="Answer",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["answer_adapter"],
        ),
    )
    handlers = build_default_handlers(
        db=object(),
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
        use_split_phases=True,
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers=handlers,
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
    )
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses["3"] == "completed"
    assert statuses["3a"] == "completed"
    assert statuses["3b"] == "skipped"
    assert tool_order == ["search_nodes", "compile_context"]
    assert result.ok
    assert result.primary_result["answer"] == "inline iterate answer"
    inline_events = [
        row
        for row in result.context.trace
        if row.get("metadata", {}).get("inline_decision_branch")
    ]
    assert any(row.get("step_id") == "3a" for row in inline_events)


def test_interpret_plan_expands_iterate_and_decision(monkeypatch):
    monkeypatch.setattr(
        "tirzah.sessions.interaction.execute_search_nodes_tool",
        lambda *_db, query, **kwargs: (
            {"matches": [{"node_id": "n1"}], "compiled_contexts": []},
            {},
        ),
    )
    plan = _plan(
        PlanStep(id="1", action="Interpret", construct="STEP"),
        PlanStep(
            id="2",
            action="ON: context_depth",
            construct="DECISION",
            depends_on=["1"],
        ),
        PlanStep(
            id="2a",
            action="Search",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["search_nodes"],
            success_criteria=["branch:search"],
        ),
        PlanStep(
            id="2b",
            action="Expand",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["expand_proximity"],
            success_criteria=["branch:expand"],
        ),
        PlanStep(
            id="3",
            action="Answer",
            construct="CALL",
            depends_on=["2a", "2b"],
            allowed_tools=["answer_adapter"],
        ),
    )
    pipeline_calls: list[int] = []

    def pipeline_executor(*_args, **_kwargs):
        pipeline_calls.append(1)
        return {"ok": True, "answer": "iterated", "used_node_ids": ["n1"]}

    handlers = build_default_handlers(
        pipeline_executor=pipeline_executor,
        db=object(),
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
        use_split_phases=False,
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers=handlers,
        config=AppConfig(),
        answer_kwargs={"focus_node_id": "focus-1", "session_id": "s1"},
    )
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses["2"] == "completed"
    assert statuses["2a"] == "completed"
    assert statuses["2b"] == "skipped"
    assert pipeline_calls == [1]
    assert result.ok
    assert result.primary_result["answer"] == "iterated"


def test_owned_by_pending_parent_hides_iterate_body():
    steps = [
        PlanStep(id="1", action="Loop", construct="ITERATE"),
        PlanStep(id="2", action="Body", construct="CALL", depends_on=["1"]),
    ]
    assert is_owned_by_pending_parent(steps[1], steps, set()) is True
    assert is_owned_by_pending_parent(steps[1], steps, {"1"}) is False


def test_suggest_plan_profile_hint_prefers_granular_for_web():
    hint = suggest_plan_profile_hint(
        "short",
        {"web_research": True},
        AppConfig(runtime=RuntimeConfig()),
    )
    assert "granular" in hint.lower()
    assert "web_search" in hint


def test_decision_cascades_skip_to_nested_branch_steps():
    steps = [
        PlanStep(id="1", action="ON: web_research", construct="DECISION"),
        PlanStep(
            id="2a",
            action="Web",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["web_search"],
            success_criteria=["branch:web"],
        ),
        PlanStep(
            id="2a1",
            action="Fetch",
            construct="CALL",
            depends_on=["2a"],
            allowed_tools=["web_fetch"],
        ),
        PlanStep(
            id="2b",
            action="Memory",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["search_nodes"],
            success_criteria=["branch:memory"],
        ),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    execute_decision_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        answer_kwargs={"web_research": False},
        config=AppConfig(runtime=RuntimeConfig(web_research_enabled=False)),
        trace=trace,
    )
    assert steps[1].status == "skipped"
    assert steps[2].status == "skipped"
    assert steps[3].status == "pending"
    assert any(
        row.get("metadata", {}).get("skipped_parents") == ["2a"]
        for row in trace
        if row.get("step_id") == "2a1"
    )


def test_iterate_break_exits_when_has_matches():
    steps = [
        PlanStep(id="1", action="Loop MAX: 5", construct="ITERATE"),
        PlanStep(id="2", action="Search", construct="CALL", depends_on=["1"], allowed_tools=["search_nodes"]),
        PlanStep(id="3", action="BREAK IF: has_matches", construct="BREAK", depends_on=["1", "2"]),
    ]
    artifacts: dict = {"context_bundle": {"tool_results": []}}
    trace: list = []
    completed: set[str] = set()
    calls = {"count": 0}

    def run_step(body_step):
        calls["count"] += 1
        artifacts["context_bundle"]["tool_results"].append(
            {"tool": "search_nodes", "ok": True, "output": {"matches": [{"node_id": "n1"}]}}
        )
        return {"status": "completed", "artifact": {"ok": True}}

    outcome = execute_iterate_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        trace=trace,
        run_step=run_step,
    )
    assert outcome["artifact"]["loop_break"] is True
    assert outcome["artifact"]["rounds"] == 1
    assert calls["count"] == 1
    assert any(row.get("metadata", {}).get("reason") == "loop_break" for row in trace)


def test_iterate_continue_advances_to_next_round():
    steps = [
        PlanStep(id="1", action="Loop MAX: 3", construct="ITERATE", success_criteria=["until:context_ready"]),
        PlanStep(id="2", action="Search", construct="CALL", depends_on=["1"], allowed_tools=["search_nodes"]),
        PlanStep(id="3", action="CONTINUE IF: round>=1", construct="CONTINUE", depends_on=["1"]),
    ]
    artifacts: dict = {"context_bundle": {"tool_results": []}}
    trace: list = []
    completed: set[str] = set()
    calls = {"count": 0}

    def run_step(body_step):
        calls["count"] += 1
        return {"status": "completed", "artifact": {"ok": True}}

    outcome = execute_iterate_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        trace=trace,
        run_step=run_step,
    )
    assert outcome["artifact"]["rounds"] >= 2
    assert calls["count"] >= 2
    assert any(row.get("metadata", {}).get("reason") == "loop_continue" for row in trace)


def test_decision_does_not_skip_merge_step_when_one_branch_selected():
    steps = [
        PlanStep(id="1", action="ON: context_depth", construct="DECISION"),
        PlanStep(
            id="2a",
            action="Search",
            construct="CALL",
            depends_on=["1"],
            success_criteria=["branch:search"],
        ),
        PlanStep(
            id="2b",
            action="Expand",
            construct="CALL",
            depends_on=["1"],
            success_criteria=["branch:expand"],
        ),
        PlanStep(id="3", action="Answer", construct="CALL", depends_on=["2a", "2b"]),
    ]
    completed: set[str] = set()
    execute_decision_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts={},
        answer_kwargs={},
        config=AppConfig(),
        trace=[],
    )
    assert steps[1].status == "pending"
    assert steps[2].status == "skipped"
    assert steps[3].status == "pending"


def test_cascade_skip_dependents_marks_nested_pending_steps():
    steps = [
        PlanStep(id="1", action="A", construct="CALL", status="skipped"),
        PlanStep(id="2", action="B", construct="CALL", depends_on=["1"]),
        PlanStep(id="3", action="C", construct="CALL", depends_on=["2"]),
    ]
    completed = {"1"}
    trace: list = []
    cascade_skip_dependents(steps, completed, trace)
    assert steps[1].status == "skipped"
    assert steps[2].status == "skipped"
    assert "2" in completed and "3" in completed


def test_evaluate_decision_branch_for_retrieval_mode():
    branch = evaluate_decision_branch(
        "retrieval_mode",
        artifacts={},
        answer_kwargs={"retrieval_mode": "agentic"},
        config=AppConfig(runtime=RuntimeConfig(retrieval_mode="direct")),
    )
    assert branch == "agentic"
