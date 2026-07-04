import time

from tirzah.config import AppConfig
from tirzah.planning.constructs import (
    execute_await_step,
    execute_concurrent_step,
    execute_parallel_step,
    execute_retry_step,
    execute_service_step,
    resume_awaiting_steps,
    retry_backoff_seconds,
)
from tirzah.planning.executor import interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep


def _plan(*steps: PlanStep) -> CairnPlan:
    return CairnPlan(
        plan_id="plan_adv",
        revision=1,
        parent_revision=None,
        request="Q",
        trigger="t",
        objective="Q",
        status="active",
        steps=list(steps),
    )


def test_retry_backoff_emits_delay(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("tirzah.planning.constructs._retry_sleeper", lambda seconds: delays.append(seconds))
    steps = [
        PlanStep(
            id="1",
            action="Retry MAX: 3 BACKOFF: linear BACKOFF_MS: 20",
            construct="RETRY",
        ),
        PlanStep(id="2", action="Work", construct="CALL", depends_on=["1"]),
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
    assert delays == [retry_backoff_seconds(2, "linear", 20)]
    assert any(row["step"] == "plan.retry.backoff" for row in trace)


def test_await_enters_pending_then_satisfies():
    step = PlanStep(
        id="1",
        action="Wait for approval EVENT: operator_approves TIMEOUT: never",
        construct="AWAIT",
    )
    artifacts: dict = {}
    trace: list = []
    pending = execute_await_step(step, answer_kwargs={}, artifacts=artifacts, trace=trace)
    assert pending["status"] == "awaiting"
    assert any(row["step"] == "plan.await.pending" for row in trace)

    completed: set[str] = set()
    step.status = "awaiting"
    resume_awaiting_steps(
        [step],
        completed,
        answer_kwargs={"await_signals": {"operator_approves": True}},
        artifacts=artifacts,
        trace=trace,
    )
    assert step.status == "completed"
    assert "1" in completed
    assert any(row["step"] == "plan.await.satisfied" for row in trace)


def test_await_timeout_blocks():
    step = PlanStep(
        id="1",
        action="Wait EVENT: signal TIMEOUT: 0.01s THEN: blocked",
        construct="AWAIT",
    )
    artifacts = {f"await:1": {"event": "signal", "status": "pending", "started_at": time.time() - 1}}
    trace: list = []
    completed: set[str] = set()
    step.status = "awaiting"
    resume_awaiting_steps([step], completed, answer_kwargs={}, artifacts=artifacts, trace=trace)
    assert step.status == "blocked"
    assert any(row["step"] == "plan.await.timeout" for row in trace)


def test_service_runs_one_tick():
    steps = [
        PlanStep(id="1", action="Worker SERVICE", construct="SERVICE"),
        PlanStep(id="2", action="Process", construct="CALL", depends_on=["1"], allowed_tools=["search_nodes"]),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls = {"count": 0}

    def run_step(body_step):
        calls["count"] += 1
        return {"status": "completed", "artifact": {"ok": True, "tick": calls["count"]}}

    outcome = execute_service_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        run_step=run_step,
        trace=trace,
        answer_kwargs={},
    )
    assert outcome["status"] == "completed"
    assert outcome["artifact"]["ticks"] == 1
    assert calls["count"] == 1
    assert any(row["step"] == "plan.service.tick" for row in trace)


def test_concurrent_runs_branches_without_merge():
    steps = [
        PlanStep(id="1", action="Fan out STATE: shared", construct="CONCURRENT"),
        PlanStep(id="1a", action="A", construct="CALL", depends_on=["1"]),
        PlanStep(id="1b", action="B", construct="CALL", depends_on=["1"]),
    ]
    artifacts: dict = {}
    trace: list = []
    completed: set[str] = set()
    calls: list[str] = []

    def branch_runner(body_step):
        calls.append(body_step.id)
        artifacts[body_step.id] = {"ok": True}
        return {"status": "completed", "artifact": artifacts[body_step.id]}

    outcome = execute_concurrent_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        branch_runner=branch_runner,
        trace=trace,
    )
    assert outcome["status"] == "completed"
    assert set(calls) == {"1a", "1b"}
    assert artifacts["concurrent:1"]["branch_ids"] == ["1a", "1b"]
    assert any(row["step"] == "plan.concurrent.completed" for row in trace)


def test_parallel_concurrent_isolated_branches():
    steps = [
        PlanStep(
            id="1",
            action="Parallel MODE: concurrent STATE: isolated",
            construct="PARALLEL",
        ),
        PlanStep(id="1a", action="A", construct="CALL", depends_on=["1"]),
        PlanStep(id="1b", action="B", construct="CALL", depends_on=["1"]),
    ]
    artifacts: dict = {"context_bundle": {"tool_results": [{"tool": "seed", "ok": True, "output": {}}]}}
    trace: list = []
    completed: set[str] = set()
    calls: list[str] = []

    def factory(local_artifacts, local_completed):
        def runner(body_step):
            calls.append(body_step.id)
            local_artifacts.setdefault("context_bundle", {"tool_results": []})
            local_artifacts["context_bundle"]["tool_results"].append(
                {"tool": body_step.id, "ok": True, "output": {}}
            )
            return {"status": "completed", "artifact": {"branch": body_step.id}}

        return runner

    outcome = execute_parallel_step(
        steps[0],
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        branch_runner=lambda _step: {"status": "completed"},
        trace=trace,
        isolated_branch_runner=factory,
    )
    assert outcome["status"] == "completed"
    assert set(calls) == {"1a", "1b"}
    assert len(artifacts["context_bundle"]["tool_results"]) == 1
    assert outcome["artifact"]["execution_mode"] == "concurrent"


def test_interpret_plan_awaiting_persists_running():
    plan = _plan(
        PlanStep(id="1", action="Wait EVENT: approval", construct="AWAIT"),
        PlanStep(id="2", action="Answer", construct="CALL", depends_on=["1"], allowed_tools=["answer_adapter"]),
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers={"answer_adapter": lambda _s, _c: {"ok": True, "answer": "done"}},
        answer_kwargs={},
    )
    statuses = {step.id: step.status for step in result.plan.steps}
    assert statuses["1"] == "awaiting"
    assert statuses["2"] == "pending"
    assert not result.ok
    assert any(row.get("step") == "plan.await.pending" for row in result.context.trace)


def test_interpret_plan_resumes_awaiting_with_signals():
    plan = _plan(
        PlanStep(id="1", action="Wait EVENT: approval", construct="AWAIT"),
        PlanStep(
            id="2",
            action="Ack",
            construct="STEP",
            depends_on=["1"],
        ),
    )
    first = interpret_plan(plan, query="Q", session_id="s1", handlers={}, answer_kwargs={})
    assert first.plan.steps[0].status == "awaiting"
    second = interpret_plan(
        first.plan,
        query="Q",
        session_id="s1",
        handlers={},
        answer_kwargs={"await_signals": {"approval": True}},
        resume_execution=False,
    )
    statuses = {step.id: step.status for step in second.plan.steps}
    assert statuses["1"] == "completed"
    assert statuses["2"] == "completed"
    assert any(row.get("step") == "plan.await.satisfied" for row in second.context.trace)


def test_interpret_plan_mid_step_revision(monkeypatch):
    revised_steps = [
        PlanStep(id="1", action="Done", construct="STEP", status="completed"),
        PlanStep(id="3", action="Added", construct="STEP", depends_on=["1"]),
    ]
    revised_plan = CairnPlan(
        plan_id="plan_adv",
        revision=2,
        parent_revision=1,
        request="Q",
        trigger="mid_step",
        objective="Q",
        status="active",
        steps=revised_steps,
        revision_decision="revise",
    )

    monkeypatch.setattr(
        "tirzah.planning.revision_runtime.revise_plan",
        lambda _plan, _info, **kwargs: revised_plan,
    )

    plan = _plan(
        PlanStep(id="1", action="Gather", construct="STEP"),
        PlanStep(id="2", action="Unused", construct="STEP", depends_on=["1"]),
    )
    result = interpret_plan(
        plan,
        query="Q",
        session_id="s1",
        handlers={},
        config=AppConfig(),
        allow_mid_revision=True,
        revision_planner=lambda _prompt: "{}",
    )
    step_ids = [step.id for step in result.plan.steps]
    assert "3" in step_ids
    assert result.plan.revision == 2
    assert any(row.get("step") == "plan.revision.mid_step" for row in result.context.trace)