from tirzah.planning.executor import build_default_handlers, interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep


def _plan(*steps: PlanStep) -> CairnPlan:
    return CairnPlan(
        plan_id="plan_test",
        revision=1,
        parent_revision=None,
        request="Do X",
        trigger="initial_request",
        objective="Do X",
        status="active",
        steps=list(steps),
    )


def test_interpret_plan_walks_depends_on_order():
    calls = []

    def pipeline(_db, _config, **kwargs):
        calls.append(kwargs.get("query"))
        return {"ok": True, "answer": "done", "used_node_ids": []}

    plan = _plan(
        PlanStep(id="1", action="Interpret", construct="STEP"),
        PlanStep(
            id="2",
            action="Gather evidence",
            construct="CALL",
            depends_on=["1"],
            allowed_tools=["tirzah_retrieval"],
        ),
        PlanStep(
            id="3",
            action="Answer",
            construct="CALL",
            depends_on=["2"],
            allowed_tools=["answer_adapter"],
        ),
        PlanStep(id="4", action="Revise later", construct="RECURSE", depends_on=["3"]),
    )
    handlers = build_default_handlers(
        pipeline_executor=pipeline,
        db=None,
        config=None,
        answer_kwargs={"session_id": "s1"},
        use_split_phases=False,
    )
    result = interpret_plan(plan, query="Do X", session_id="s1", handlers=handlers)
    assert result.ok
    assert calls == ["Do X"]  # retrieval once; answer_adapter reuses artifact
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses == {"1": "completed", "2": "completed", "3": "completed", "4": "skipped"}
    assert result.primary_result["answer"] == "done"
    assert any(row["step"] == "plan.step.started" for row in result.context.trace)


def test_call_without_handler_is_blocked():
    plan = _plan(
        PlanStep(
            id="1",
            action="Mystery",
            construct="CALL",
            allowed_tools=["compile_context"],
        ),
    )
    result = interpret_plan(plan, query="q", session_id="s1", handlers={})
    assert not result.ok
    assert result.plan.steps[0].status == "blocked"


def test_duplicate_retrieval_call_is_skipped():
    seen = {"count": 0}

    def pipeline(_db, _config, **kwargs):
        seen["count"] += 1
        return {"ok": True, "answer": "once"}

    plan = _plan(
        PlanStep(id="1", action="A", construct="CALL", allowed_tools=["tirzah_retrieval"]),
        PlanStep(id="2", action="B", construct="CALL", allowed_tools=["tirzah_retrieval"]),
    )
    handlers = build_default_handlers(
        pipeline_executor=pipeline,
        db=None,
        config=None,
        answer_kwargs={},
        use_split_phases=False,
    )
    result = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert seen["count"] == 1
    assert result.plan.steps[0].status == "completed"
    assert result.plan.steps[1].status == "skipped"


def test_interpretive_wrapper_uses_split_phases(monkeypatch):
    from tirzah.config import AppConfig, RuntimeConfig
    from tirzah.planning.recursive import process_frontend_request

    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan",
        lambda *a, **k: _plan(
            PlanStep(id="1", action="Retrieve", construct="CALL", allowed_tools=["tirzah_retrieval"]),
            PlanStep(
                id="2",
                action="Synthesize",
                construct="CALL",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ),
    )
    monkeypatch.setattr("tirzah.planning.recursive.revise_plan_recursively", lambda plan, *a, **k: [plan])
    monkeypatch.setattr("tirzah.planning.recursive.save_plan_revision", lambda *a, **k: None)

    monkeypatch.setattr(
        "tirzah.sessions.answer_phases.retrieve_for_answer",
        lambda _db, _config, **kwargs: {
            "ok": True,
            "package": {
                "query": kwargs["query"],
                "session_id": kwargs.get("session_id", "default"),
                "focus_node_id": None,
                "selected_node_id": None,
                "retrieval_mode": "direct",
                "runtime_config": {"answer_adapter": "mock"},
                "process_trace": [],
                "prompt": {"prompt_text": "ctx", "budget": {}, "context_metadata": {}},
                "retrieval_status": "matched_context",
            },
        },
    )
    monkeypatch.setattr(
        "tirzah.sessions.answer_phases.synthesize_from_retrieval",
        lambda _db, _config, _package: {"ok": True, "answer": "via-plan", "used_node_ids": []},
    )

    cfg = AppConfig(
        runtime=RuntimeConfig(
            recursive_planning_enabled=True,
            plan_interpretive_execution_enabled=True,
        )
    )
    result = process_frontend_request(
        None,
        cfg,
        query="hello",
        executor=lambda *_a, **_k: {"ok": True, "answer": "unused"},
        planner=lambda _prompt: "{}",
        session_id="s1",
    )
    assert result["answer"] == "via-plan"
    assert any(str(row.get("step", "")).startswith("plan.step.") for row in result.get("process_trace", []))