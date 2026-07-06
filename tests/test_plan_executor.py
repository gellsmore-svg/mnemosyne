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


def test_failed_retrieval_does_not_consume_once_only_effect():
    calls = {"count": 0}

    def pipeline(_db, _config, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"ok": False, "reason": "temporary_failure"}
        return {"ok": True, "answer": "retried"}

    plan = _plan(
        PlanStep(id="1", action="A", construct="CALL", allowed_tools=["tirzah_retrieval"]),
    )
    handlers = build_default_handlers(
        pipeline_executor=pipeline,
        db=None,
        config=None,
        answer_kwargs={},
        use_split_phases=False,
    )

    first = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert first.plan.steps[0].status == "blocked"
    assert "tirzah_retrieval" not in first.context.effects

    plan.steps[0].status = "pending"
    second = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert second.plan.steps[0].status == "completed"
    assert calls["count"] == 2


def test_specialist_call_dispatches_registered_handler():
    plan = _plan(
        PlanStep(id="1", action="Check coherence", construct="CALL", allowed_tools=["coherence_check"]),
    )

    def handler(step, ctx):
        return {"ok": True, "tool": "coherence_check", "query": ctx.query, "action": step.action}

    result = interpret_plan(plan, query="q", session_id="s1", handlers={"coherence_check": handler})
    assert result.ok
    assert result.plan.steps[0].status == "completed"
    assert result.context.artifacts["1"]["tool"] == "coherence_check"


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


def test_interpretive_mode_executes_each_revised_plan(monkeypatch):
    from tirzah.config import AppConfig, RuntimeConfig
    from tirzah.planning.executor import PlanExecutionContext, PlanExecutionResult
    from tirzah.planning.recursive import CairnPlan, PlanStep, process_frontend_request

    revised_plan = CairnPlan(
        plan_id="plan_test",
        revision=2,
        parent_revision=1,
        request="hello",
        trigger="execution_evidence",
        objective="hello",
        status="stable",
        steps=[
            PlanStep(id="1", action="Revised step", construct="STEP"),
            PlanStep(
                id="2",
                action="Answer revised",
                construct="CALL",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ],
        revision_decision="revise",
        revision_reason="needs revised execution",
    )
    interpreted_revisions: list[int] = []

    def fake_interpret(plan, **kwargs):
        interpreted_revisions.append(plan.revision)
        answer = "rev1" if plan.revision == 1 else "rev2"
        context = PlanExecutionContext(
            query=kwargs.get("query", "hello"),
            session_id=kwargs.get("session_id", "s1"),
            artifacts={"synthesis_result": {"ok": True, "answer": answer, "used_node_ids": []}},
            trace=[{"step": "plan.step.completed", "step_id": "1", "metadata": {"revision": plan.revision}}],
        )
        return PlanExecutionResult(
            ok=True,
            plan=plan,
            context=context,
            primary_result={"ok": True, "answer": answer, "used_node_ids": []},
        )

    monkeypatch.setattr("tirzah.planning.executor.interpret_plan", fake_interpret)
    stable_plan = CairnPlan(
        plan_id="plan_test",
        revision=3,
        parent_revision=2,
        request="hello",
        trigger="execution_evidence",
        objective="hello",
        status="stable",
        steps=revised_plan.steps,
        revision_decision="stable",
        revision_reason="done",
    )

    def fake_revise(plan, _info, **kwargs):
        if plan.revision == 1:
            return revised_plan
        return stable_plan

    monkeypatch.setattr("tirzah.planning.recursive.revise_plan", fake_revise)
    monkeypatch.setattr(
        "tirzah.planning.execution_store.get_plan_execution",
        lambda _db, plan_id, revision, session_id: {
            "plan_id": plan_id,
            "revision": revision,
            "session_id": session_id,
            "status": "completed",
            "artifacts": {},
            "steps": [],
            "completed_step_ids": ["1"],
        },
    )
    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan",
        lambda *a, **k: _plan(
            PlanStep(id="1", action="Gather", construct="CALL", allowed_tools=["tirzah_retrieval"]),
            PlanStep(
                id="2",
                action="Answer",
                construct="CALL",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ),
    )
    monkeypatch.setattr("tirzah.planning.recursive.save_plan_revision", lambda *a, **k: None)

    cfg = AppConfig(
        runtime=RuntimeConfig(
            recursive_planning_enabled=True,
            plan_interpretive_execution_enabled=True,
            planning_max_revisions=3,
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
    assert interpreted_revisions == [1, 2]
    assert result["answer"] == "rev2"
    assert result["request_plan"]["revision"] == 3
    assert any(row.get("step") == "plan.revision.proposed" for row in result.get("process_trace", []))
    assert any(row.get("step") == "plan.revision.executed" for row in result.get("process_trace", []))


def test_interpretive_wrapper_exposes_execution_and_bundle_summary(monkeypatch):
    from tirzah.config import AppConfig, RuntimeConfig
    from tirzah.planning.executor import PlanExecutionContext, PlanExecutionResult
    from tirzah.planning.recursive import process_frontend_request

    def fake_interpret(plan, **kwargs):
        context = PlanExecutionContext(
            query=kwargs.get("query", "hello"),
            session_id=kwargs.get("session_id", "s1"),
            artifacts={
                "context_bundle": {
                    "tool_results": [{"tool": "search_nodes", "ok": True, "output": {"matches": []}}],
                },
                "synthesis_result": {"ok": True, "answer": "bundled", "used_node_ids": ["n1"]},
            },
            trace=[{"step": "plan.step.completed", "step_id": "1", "metadata": {}}],
        )
        return PlanExecutionResult(
            ok=True,
            plan=plan,
            context=context,
            primary_result=context.artifacts["synthesis_result"],
        )

    monkeypatch.setattr("tirzah.planning.executor.interpret_plan", fake_interpret)
    monkeypatch.setattr(
        "tirzah.planning.execution_store.get_plan_execution",
        lambda _db, plan_id, revision, session_id: {
            "plan_id": plan_id,
            "revision": revision,
            "session_id": session_id,
            "status": "completed",
            "artifacts": {"context_bundle": {"tool_results": []}},
            "steps": [],
            "completed_step_ids": ["1"],
        },
    )
    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan",
        lambda *a, **k: _plan(
            PlanStep(id="1", action="Search", construct="CALL", allowed_tools=["search_nodes"]),
            PlanStep(
                id="2",
                action="Answer",
                construct="CALL",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ),
    )
    monkeypatch.setattr("tirzah.planning.recursive.revise_plan_recursively", lambda plan, *a, **k: [plan])
    monkeypatch.setattr("tirzah.planning.recursive.save_plan_revision", lambda *a, **k: None)

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
    assert result["answer"] == "bundled"
    assert result["context_bundle_summary"]["tools"] == ["search_nodes"]
    assert result["plan_execution"]["plan_id"] == "plan_test"
    assert result["plan_execution"]["status"] == "completed"

def test_interpret_plan_streams_trace_live_and_bridge_skips_duplicates():
    """With a live tracer, every executor trace entry is published in real time
    (bus + store) and marked `live`, so the post-hoc process_trace bridge does
    not emit it a second time."""
    from galeed.bus import TraceBus
    from galeed.recorder import Tracer

    from tirzah.sessions.process_events import emit_process_trace_events

    bus = TraceBus()
    live_events = []
    with bus.subscribe("*") as subscription:
        tracer = Tracer(session_id="s1", db=None, bus=bus, source="tirzah")
        plan = _plan(PlanStep(id="1", action="Interpret", construct="STEP"))
        execution = interpret_plan(
            plan, query="q", session_id="s1", handlers={}, tracer=tracer
        )
        while not subscription.empty():
            live_events.append(subscription.get_nowait())

    types = [event.type for event in live_events]
    assert "plan.step.started" in types
    assert any(t.startswith("plan.step.") and t != "plan.step.started" for t in types)
    # every executor trace entry carries the live marker…
    assert execution.context.trace and all(e.get("live") for e in execution.context.trace)

    # …so the post-hoc bridge emits nothing for them (no duplicates).
    before = len(tracer.events)
    emit_process_trace_events(tracer, list(execution.context.trace))
    assert len(tracer.events) == before


def test_interpret_plan_without_tracer_keeps_bridge_emission():
    """No tracer → entries are not marked live and the bridge still emits them
    (backwards-compatible post-hoc behaviour)."""
    from galeed.recorder import Tracer

    from tirzah.sessions.process_events import emit_process_trace_events

    plan = _plan(PlanStep(id="1", action="Interpret", construct="STEP"))
    execution = interpret_plan(plan, query="q", session_id="s1", handlers={})
    assert execution.context.trace and not any(e.get("live") for e in execution.context.trace)

    tracer = Tracer(session_id="s1", db=None, source="tirzah")
    emit_process_trace_events(tracer, list(execution.context.trace))
    # the bridge maps plan.* names onto process.step events, name in metadata
    assert any(
        str(event.metadata.get("step", "")).startswith("plan.step.")
        for event in tracer.events
    )
