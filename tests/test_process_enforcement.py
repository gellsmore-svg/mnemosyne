"""Process enforcement — constraint rendering, gates, deviations, override."""

from __future__ import annotations

import pytest

from tests.process_fakes import FakeDb
from tirzah.planning.execution_store import save_plan_execution
from tirzah.planning.recursive import CairnPlan, PlanStep
from tirzah.process import enforcement as enf
from tirzah.process import instances as inst
from tirzah.process import templates as tmpl


def _instance(db, body: str) -> dict:
    t = tmpl.create_template(db, name="P", body=body, risk_level="low")
    return inst.start_instance(db, template_id=t["template_id"], task="X", session_id="s1")


def test_gate_and_override_detection() -> None:
    assert enf.process_requires_gate("… PAUSE FOR APPROVAL before shipping …")
    assert enf.process_requires_gate("step 3 requires approval")
    assert not enf.process_requires_gate("just do the thing and log it")
    assert enf.process_is_override("normal gates are suspended; act immediately")


def test_render_constraint_leads_with_process_and_asks_for_gate_steps() -> None:
    db = FakeDb()
    instance = _instance(db, "1. plan\n2. PAUSE FOR APPROVAL before applying")
    block = enf.render_process_constraint(instance)
    assert block.startswith("ACTIVE PROCESS")
    assert "PAUSE FOR APPROVAL" in block
    assert "AWAIT step" in block  # gate instruction present
    assert "material deviation" in block.lower()


def test_render_constraint_notes_override_processes() -> None:
    db = FakeDb()
    instance = _instance(db, "Act immediately; normal gates are suspended.")
    block = enf.render_process_constraint(instance)
    assert "emergency/override" in block.lower()
    assert "retrospective is" in block.lower()


def test_gate_pauses_and_approval_resumes() -> None:
    db = FakeDb()
    instance = _instance(db, "PAUSE FOR APPROVAL")
    iid = instance["instance_id"]

    paused = enf.reach_gate(db, iid, step_id="3", reason="about to ship")
    assert paused["status"] == "awaiting_gate"
    assert paused["trace"][-1]["event"] == "process.gate.reached"

    resumed = enf.resolve_gate(db, iid, step_id="3", approved=True, note="looks good")
    assert resumed["status"] == "active"
    assert resumed["trace"][-1]["event"] == "process.gate.approved"

    rejected = enf.resolve_gate(db, iid, step_id="3", approved=False)
    assert rejected["status"] == "active"  # returns to flow for iteration
    assert rejected["trace"][-1]["event"] == "process.gate.rejected"


def test_gate_approval_signals_matching_plan_await() -> None:
    db = FakeDb()
    instance = _instance(db, "PAUSE FOR APPROVAL")
    iid = instance["instance_id"]
    plan = CairnPlan(
        plan_id="plan_gate",
        revision=1,
        parent_revision=None,
        request="q",
        trigger="t",
        objective="q",
        status="active",
        steps=[PlanStep(id="3", action="EVENT: approval", construct="AWAIT", status="awaiting")],
    )
    save_plan_execution(
        db,
        plan=plan,
        session_id="s1",
        query="q",
        steps=plan.steps,
        completed_step_ids=[],
        artifacts={},
        trace=[],
        effects=[],
        status="running",
    )

    resumed = enf.resolve_gate(db, iid, step_id="3", approved=True, note="approved")

    assert resumed["trace"][-1]["detail"]["plan_resume"]["resumed"] is True
    saved = db.plan_executions.rows[0]
    assert saved["steps"][0]["status"] == "completed"


def test_deviation_flag_pauses_until_resolved() -> None:
    db = FakeDb()
    instance = _instance(db, "Fluid — just proceed")
    iid = instance["instance_id"]
    flagged = enf.flag_deviation(db, iid, description="skipping the review loop", step_id="2")
    assert flagged["status"] == "awaiting_gate"
    approved = enf.resolve_deviation(db, iid, approved=True, note="acceptable here")
    assert approved["status"] == "active"
    assert approved["trace"][-1]["event"] == "process.deviation.approved"


def test_override_requires_justification() -> None:
    db = FakeDb()
    instance = _instance(db, "Emergency; gates suspended")
    iid = instance["instance_id"]
    with pytest.raises(ValueError):
        enf.record_override(db, iid, justification="   ")
    recorded = enf.record_override(db, iid, justification="prod is down, must patch now")
    assert recorded["trace"][-1]["event"] == "process.override.invoked"
    assert recorded["trace"][-1]["detail"]["justification"].startswith("prod is down")


def test_note_plan_shaped_records_adherence_signal() -> None:
    db = FakeDb()
    instance = _instance(db, "PAUSE FOR APPROVAL")
    enf.note_plan_shaped(db, instance["instance_id"], plan_id="plan_1", has_gate_steps=True)
    reloaded = inst.get_instance(db, instance["instance_id"])
    shaped = [e for e in reloaded["trace"] if e["event"] == "process.plan.shaped"]
    assert shaped and shaped[-1]["detail"]["has_gate_steps"] is True
