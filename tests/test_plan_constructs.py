from tirzah.config import AppConfig, RuntimeConfig
from tirzah.planning.constructs import (
    cascade_skip_dependents,
    evaluate_decision_branch,
    execute_decision_step,
    execute_iterate_step,
    is_owned_by_pending_parent,
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