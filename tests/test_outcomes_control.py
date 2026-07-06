"""Outcomes-validation loop — phase 2 (live control: reanchor + drift gate)."""

from __future__ import annotations

from tests.process_fakes import FakeDb
from tirzah.planning.outcomes_control import (
    OutcomesController,
    active_outcomes_controller,
)
from tirzah.process import instances as inst
from tirzah.process import outcomes as oc
from tirzah.process import templates as tmpl

_OUTCOMES = [
    {"id": "O1", "statement": "cite the fatigue dataset",
     "check": "fatigue dataset named in evidence"},
    {"id": "O2", "statement": "frame order-effect magnitude as a lens"},
]

_ALIGNED = {"answer": "We cite the fatigue dataset named in evidence; order-effect "
                      "magnitude is framed as a lens."}
_DRIFTED = {"answer": "An unrelated note about weather and gardening."}


def _armed_instance(db, on_drift="reanchor_then_gate", session_id="s1"):
    t = tmpl.create_template(
        db, name="Anchored", body="1. do\n2. validate outcomes",
        outcomes=_OUTCOMES, outcomes_loop={"on_drift": on_drift},
    )
    return inst.start_instance(db, template_id=t["template_id"], task="t", session_id=session_id)


def test_controller_only_active_when_loop_armed() -> None:
    db = FakeDb()
    assert active_outcomes_controller(db, None) is None
    assert active_outcomes_controller(db, "nope") is None
    # instance without a loop → no controller
    plain = tmpl.create_template(db, name="Plain", body="do")
    inst.start_instance(db, template_id=plain["template_id"], task="t", session_id="s0")
    assert active_outcomes_controller(db, "s0") is None
    # armed instance → controller
    _armed_instance(db, session_id="s1")
    assert isinstance(active_outcomes_controller(db, "s1"), OutcomesController)


def test_assess_records_events_and_caches() -> None:
    db = FakeDb()
    instance = _armed_instance(db)
    ctrl = active_outcomes_controller(db, "s1")

    aligned = ctrl.assess(_ALIGNED)
    assert aligned["drift_score"] == 0.0 and aligned["drifting"] is False
    assert ctrl.last is aligned

    drifted = ctrl.assess(_DRIFTED)
    assert drifted["drifting"] is True

    events = [e["event"] for e in inst.get_instance(db, instance["instance_id"])["trace"]]
    assert oc.OUTCOMES_VALIDATED in events
    assert oc.OUTCOMES_MET in events      # from the aligned pass
    assert oc.OUTCOMES_DRIFT in events    # from the drifted pass


def test_reanchor_injects_only_when_drifting_and_enabled() -> None:
    db = FakeDb()
    _armed_instance(db, on_drift="reanchor_then_gate")
    ctrl = active_outcomes_controller(db, "s1")

    drift = ctrl.assess(_DRIFTED)
    info = ctrl.reanchor_information({"ok": True}, drift)
    assert "outcome_reanchor" in info and "O1" in info["outcome_reanchor"]

    aligned = ctrl.assess(_ALIGNED)
    assert "outcome_reanchor" not in ctrl.reanchor_information({"ok": True}, aligned)


def test_reanchor_skipped_for_gate_only_and_log() -> None:
    for on_drift in ("gate", "log"):
        db = FakeDb()
        _armed_instance(db, on_drift=on_drift, session_id="sx")
        ctrl = active_outcomes_controller(db, "sx")
        drift = ctrl.assess(_DRIFTED)
        assert "outcome_reanchor" not in ctrl.reanchor_information({"ok": True}, drift)


def test_should_gate_completion_respects_on_drift() -> None:
    db = FakeDb()
    _armed_instance(db, on_drift="reanchor", session_id="soft")
    soft = active_outcomes_controller(db, "soft")
    assert soft.should_gate_completion(soft.assess(_DRIFTED)) is False  # reanchor never gates

    db2 = FakeDb()
    _armed_instance(db2, on_drift="gate", session_id="hard")
    hard = active_outcomes_controller(db2, "hard")
    assert hard.should_gate_completion(hard.assess(_DRIFTED)) is True
    assert hard.should_gate_completion(hard.assess(_ALIGNED)) is False  # not drifting


def test_never_gate_on_model_judgement_alone() -> None:
    """Deterministic floor says met; only the model says unmet → no gate."""
    db = FakeDb()
    instance = _armed_instance(db, on_drift="gate")
    ctrl = OutcomesController(
        db, inst.get_instance(db, instance["instance_id"]),
        ask=lambda _p: '[{"id":"O1","status":"unmet"},{"id":"O2","status":"unmet"}]',
    )
    validation = ctrl.assess(_ALIGNED)  # text covers the outcomes → floor = met
    assert validation["drifting"] is True            # model pushed drift up
    assert ctrl.should_gate_completion(validation) is False  # but floor disagrees → no gate


def test_raise_drift_gate_pauses_instance() -> None:
    db = FakeDb()
    instance = _armed_instance(db, on_drift="gate")
    ctrl = active_outcomes_controller(db, "s1")
    summary = ctrl.raise_drift_gate(ctrl.assess(_DRIFTED))

    assert summary["gate"] == "outcomes_drift"
    assert {o["id"] for o in summary["unmet_outcomes"]} == {"O1", "O2"}
    assert inst.get_instance(db, instance["instance_id"])["status"] == "awaiting_gate"


def test_llm_judge_wires_ask_from_config(monkeypatch) -> None:
    """A loop with judge='llm' builds the model ask from config; falls back to
    the deterministic floor when no config is given."""
    db = FakeDb()
    t = tmpl.create_template(
        db, name="Judged", body="do", outcomes=_OUTCOMES,
        outcomes_loop={"on_drift": "gate", "judge": "llm"},
    )
    inst.start_instance(db, template_id=t["template_id"], task="t", session_id="sj")

    monkeypatch.setattr(
        "tirzah.process.refinement.default_ask",
        lambda _config: (lambda _p: '[{"id":"O1","status":"unmet"},{"id":"O2","status":"unmet"}]'),
    )
    ctrl = active_outcomes_controller(db, "sj", config=object())
    assert ctrl.ask is not None
    # deterministic judge (no config) → no ask
    assert active_outcomes_controller(db, "sj").ask is None


# --- integration: the guarded hooks in the recursive planner ---------------


def test_recursive_planner_gates_drifting_completion(monkeypatch):
    """A revision proposes 'complete' while drifting → the controller gates it
    and re-anchor is injected on the way there."""
    from tirzah.config import AppConfig, RuntimeConfig
    from tirzah.planning.executor import PlanExecutionContext, PlanExecutionResult
    from tirzah.planning.recursive import CairnPlan, PlanStep, process_frontend_request

    reanchor_calls: list[bool] = []

    class StubController:
        def assess(self, _result):
            return {"drifting": True, "per_outcome": [
                {"id": "O1", "statement": "cite dataset", "status": "unmet",
                 "deterministic_status": "unmet"}]}

        def reanchor_information(self, information, _v):
            reanchor_calls.append(True)
            return {**information, "outcome_reanchor": "re-align to O1"}

        def should_gate_completion(self, _v):
            return True

        def raise_drift_gate(self, _v):
            return {"gate": "outcomes_drift", "step_id": "outcomes:drift"}

    monkeypatch.setattr(
        "tirzah.planning.recursive._init_outcomes_controller",
        lambda _db, _sid, _cfg=None: StubController(),
    )

    def fake_interpret(plan, **kwargs):
        return PlanExecutionResult(
            ok=True, plan=plan,
            context=PlanExecutionContext(query="q", session_id="s1", artifacts={}, trace=[]),
            primary_result={"ok": True, "answer": "drifted answer", "used_node_ids": []},
        )

    complete_plan = CairnPlan(
        plan_id="p", revision=2, parent_revision=1, request="q",
        trigger="t", objective="q", status="complete",
        steps=[PlanStep(id="1", action="ans", construct="CALL", allowed_tools=["answer_adapter"])],
        revision_decision="complete", revision_reason="claims done",
    )
    monkeypatch.setattr("tirzah.planning.executor.interpret_plan", fake_interpret)
    monkeypatch.setattr("tirzah.planning.recursive.revise_plan", lambda *a, **k: complete_plan)
    monkeypatch.setattr("tirzah.planning.recursive.save_plan_revision", lambda *a, **k: None)
    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan",
        lambda *a, **k: CairnPlan(
            plan_id="p", revision=1, parent_revision=None, request="q", trigger="initial_request",
            objective="q", status="active",
            steps=[PlanStep(id="1", action="ans", construct="CALL", allowed_tools=["answer_adapter"])],
        ),
    )
    monkeypatch.setattr(
        "tirzah.planning.execution_store.get_plan_execution", lambda *a, **k: None
    )

    cfg = AppConfig(runtime=RuntimeConfig(
        recursive_planning_enabled=True,
        plan_interpretive_execution_enabled=True,
        planning_max_revisions=3,
    ))
    result = process_frontend_request(
        None, cfg, query="q",
        executor=lambda *_a, **_k: {"ok": True, "answer": "unused"},
        planner=lambda _p: "{}", session_id="s1",
    )

    assert result.get("outcomes_gate", {}).get("gate") == "outcomes_drift"
    assert reanchor_calls  # re-anchor was injected before the completing revision
    assert any(row.get("step") == "outcomes.gate" for row in result.get("process_trace", []))
