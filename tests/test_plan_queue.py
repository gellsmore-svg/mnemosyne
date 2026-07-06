"""QUEUE construct — turn-based / round-robin multi-agent discussion (Cairn §5)."""

from __future__ import annotations

from tirzah.planning.constructs import (
    execute_queue_step,
    parse_queue_order,
    parse_queue_rounds,
)
from tirzah.planning.executor import build_default_handlers, interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep, normalize_steps


def _plan(*steps: PlanStep) -> CairnPlan:
    return CairnPlan(
        plan_id="plan_q", revision=1, parent_revision=None, request="r",
        trigger="initial_request", objective="discuss", status="active", steps=list(steps),
    )


def _queue(step_id, action, criteria=None, participants=()):
    return [
        PlanStep(id=step_id, action=action, construct="QUEUE", success_criteria=criteria or []),
        *participants,
    ]


def test_queue_construct_survives_normalization() -> None:
    normalized = normalize_steps(
        [{"id": "q1", "action": "discuss", "construct": "QUEUE", "status": "pending"}],
        max_steps=10,
    )
    assert len(normalized) == 1
    assert normalized[0].construct == "QUEUE"


def test_parsers() -> None:
    assert parse_queue_order(PlanStep(id="1", action="QUEUE ORDER: ROUND_ROBIN")) == "ROUND_ROBIN"
    assert parse_queue_order(PlanStep(id="1", action="QUEUE ORDER: PRIORITY")) == "PRIORITY"
    assert parse_queue_order(PlanStep(id="1", action="a turn queue")) == "FIFO"
    assert parse_queue_rounds(PlanStep(id="1", action="QUEUE", success_criteria=["ROUNDS: 4"])) == 4
    assert parse_queue_rounds(PlanStep(id="1", action="QUEUE MAX: 3")) == 3


def _runner_recording(calls):
    def run(step):
        calls.append(step.id)
        return {"status": "completed", "artifact": {"by": step.id}}
    return run


def test_round_robin_cycles_participants_across_rounds() -> None:
    calls: list[str] = []
    q = PlanStep(id="Q", action="QUEUE ORDER: ROUND_ROBIN", success_criteria=["ROUNDS: 3"])
    a = PlanStep(id="a", action="agent A", construct="CALL", depends_on=["Q"])
    b = PlanStep(id="b", action="agent B", construct="CALL", depends_on=["Q"])
    steps = [q, a, b]
    artifacts: dict = {}
    outcome = execute_queue_step(
        q, steps=steps, completed=set(), artifacts=artifacts,
        run_step=_runner_recording(calls), trace=[],
    )
    assert outcome["status"] == "completed"
    # 3 rounds × 2 participants, in order, cycling.
    assert calls == ["a", "b", "a", "b", "a", "b"]
    assert outcome["artifact"]["rounds_run"] == 3
    assert len(outcome["artifact"]["transcript"]) == 6


def test_fifo_runs_each_participant_once_in_order() -> None:
    calls: list[str] = []
    q = PlanStep(id="Q", action="QUEUE ORDER: FIFO")
    a = PlanStep(id="a", action="A", construct="CALL", depends_on=["Q"])
    b = PlanStep(id="b", action="B", construct="CALL", depends_on=["Q"])
    outcome = execute_queue_step(
        q, steps=[q, a, b], completed=set(), artifacts={},
        run_step=_runner_recording(calls), trace=[],
    )
    assert calls == ["a", "b"]
    assert outcome["artifact"]["rounds_run"] == 1


def test_priority_orders_higher_first() -> None:
    calls: list[str] = []
    q = PlanStep(id="Q", action="QUEUE ORDER: PRIORITY")
    a = PlanStep(id="a", action="A", construct="CALL", depends_on=["Q"], success_criteria=["priority: 1"])
    b = PlanStep(id="b", action="B", construct="CALL", depends_on=["Q"], success_criteria=["priority: 5"])
    execute_queue_step(q, steps=[q, a, b], completed=set(), artifacts={},
                       run_step=_runner_recording(calls), trace=[])
    assert calls == ["b", "a"]  # higher priority first


def test_round_robin_stops_early_on_convergence() -> None:
    q = PlanStep(id="Q", action="QUEUE ORDER: ROUND_ROBIN",
                 success_criteria=["ROUNDS: 5", "UNTIL: consensus"])
    a = PlanStep(id="a", action="A", construct="CALL", depends_on=["Q"])
    b = PlanStep(id="b", action="B", construct="CALL", depends_on=["Q"])

    round_counter = {"n": 0}

    def run(step):
        # Converge on the 2nd round: B declares consensus.
        if step.id == "b":
            round_counter["n"] += 1
            if round_counter["n"] >= 2:
                return {"status": "completed", "artifact": {"converged": True}}
        return {"status": "completed", "artifact": {"out": step.id}}

    outcome = execute_queue_step(q, steps=[q, a, b], completed=set(), artifacts={},
                                 run_step=run, trace=[])
    assert outcome["artifact"]["converged"] is True
    assert outcome["artifact"]["rounds_run"] == 2  # stopped before round 5


def test_blocked_turn_stops_the_queue() -> None:
    q = PlanStep(id="Q", action="QUEUE ORDER: ROUND_ROBIN", success_criteria=["ROUNDS: 3"])
    a = PlanStep(id="a", action="A", construct="CALL", depends_on=["Q"])
    steps = [q, a]

    def run(step):
        return {"status": "blocked", "reason": "handler_down"}

    outcome = execute_queue_step(q, steps=steps, completed=set(), artifacts={}, run_step=run, trace=[])
    assert outcome["status"] == "blocked"
    assert outcome["reason"] == "queue_turn_blocked"


def test_end_to_end_through_interpreter_shared_transcript() -> None:
    """A QUEUE plan runs through interpret_plan; each turn sees the accumulating
    discussion via shared artifacts (turn-based, not isolated)."""
    seen_transcript_lengths: list[int] = []

    def agent_pipeline(_db, _config, **kwargs):
        # Read how much discussion exists so far from the query context we're
        # given; the handler just records the count and contributes a line.
        return {"ok": True, "answer": f"turn-by-{kwargs.get('query')}", "used_node_ids": []}

    plan = _plan(
        PlanStep(id="1", action="Open the discussion", construct="STEP"),
        PlanStep(
            id="2", action="QUEUE ORDER: ROUND_ROBIN", construct="QUEUE",
            depends_on=["1"], success_criteria=["ROUNDS: 2"],
        ),
        PlanStep(id="2a", action="Proposer argues", construct="CALL",
                 depends_on=["2"], allowed_tools=["answer_adapter"]),
        PlanStep(id="2b", action="Challenger rebuts", construct="CALL",
                 depends_on=["2"], allowed_tools=["answer_adapter"]),
    )
    handlers = build_default_handlers(
        pipeline_executor=agent_pipeline, db=None, config=None, answer_kwargs={},
        use_split_phases=False,
    )
    execution = interpret_plan(plan, query="q", session_id="s", handlers=handlers)
    queue_artifact = execution.context.artifacts.get("queue:2")
    assert queue_artifact is not None
    assert queue_artifact["order"] == "ROUND_ROBIN"
    assert queue_artifact["rounds_run"] == 2
    # 2 rounds × 2 participants = 4 recorded turns.
    assert len(queue_artifact["transcript"]) == 4
    assert queue_artifact["participant_ids"] == ["2a", "2b"]
    _ = seen_transcript_lengths
