"""Auto-evolution of process templates from retrospective data."""

from __future__ import annotations

from tests.process_fakes import FakeDb
from tirzah.process import enforcement as enf
from tirzah.process import evolution as evo
from tirzah.process import instances as inst
from tirzah.process import templates as tmpl


def _template(db) -> str:
    t = tmpl.create_template(db, name="Flow", body="1. plan\n2. PAUSE FOR APPROVAL", risk_level="medium")
    return t["template_id"]


def _run(db, tid, *, deviation=None, deviation_approved=False, gate_reject_step=None,
         override=False, abandoned=False, completed=True):
    instance = inst.start_instance(db, template_id=tid, task="t")
    iid = instance["instance_id"]
    if deviation:
        enf.flag_deviation(db, iid, description=deviation)
        if deviation_approved:
            enf.resolve_deviation(db, iid, approved=True)
    if gate_reject_step:
        enf.reach_gate(db, iid, step_id=gate_reject_step)
        enf.resolve_gate(db, iid, step_id=gate_reject_step, approved=False)
    if override:
        enf.record_override(db, iid, justification="urgent")
    if abandoned:
        inst.abandon_instance(db, iid, reason="descoped")
    elif completed:
        inst.complete_instance(db, iid, outcome="shipped")
    return iid


def test_not_ready_with_too_little_history() -> None:
    db = FakeDb()
    tid = _template(db)
    _run(db, tid)
    result = evo.analyze_template_evolution(db, tid)
    assert result["ready"] is False
    assert "at least" in result["reason"]


def test_recurring_approved_deviation_is_flagged_to_fold_in() -> None:
    db = FakeDb()
    tid = _template(db)
    for _ in range(3):
        _run(db, tid, deviation="skip the second review", deviation_approved=True)
    result = evo.analyze_template_evolution(db, tid)
    assert result["ready"] is True
    fold = [f for f in result["findings"] if f["kind"] == "fold_deviation"]
    assert fold and fold[0]["evidence"]["approved_count"] == 3
    assert "skip the second review" in fold[0]["evidence"]["description"]


def test_gate_friction_and_abandonment_signals() -> None:
    db = FakeDb()
    tid = _template(db)
    _run(db, tid, gate_reject_step="3")   # step 3 gate rejected twice → friction
    _run(db, tid, gate_reject_step="3")
    _run(db, tid, abandoned=True)         # 2/4 = 0.5 abandonment → high
    _run(db, tid, abandoned=True)
    result = evo.analyze_template_evolution(db, tid)
    kinds = {f["kind"] for f in result["findings"]}
    assert "gate_friction" in kinds
    assert "high_abandonment" in kinds


def test_override_rate_threshold() -> None:
    db = FakeDb()
    tid = _template(db)
    # 2 overrides of 3 instances = 0.67 → above threshold.
    _run(db, tid, override=True)
    _run(db, tid, override=True)
    _run(db, tid)
    result = evo.analyze_template_evolution(db, tid)
    heavy = [f for f in result["findings"] if f["kind"] == "gates_too_heavy"]
    assert heavy and heavy[0]["evidence"]["overrides"] == 2


def test_propose_deterministic_appends_evolution_notes() -> None:
    db = FakeDb()
    tid = _template(db)
    for _ in range(3):
        _run(db, tid, deviation="attach the test output", deviation_approved=True)
    proposal = evo.propose_evolution(db, tid)
    assert proposal["ready"] is True
    assert "Evolution notes" in proposal["proposed_body"]
    assert "attach the test output" in proposal["proposed_body"]
    assert proposal["model_used"] is False
    assert "fold_deviation" in proposal["rationale"]


def test_propose_uses_model_rewrite_when_available() -> None:
    db = FakeDb()
    tid = _template(db)
    for _ in range(3):
        _run(db, tid, deviation="d", deviation_approved=True)

    def ask(_prompt: str) -> str:
        return "1. plan\n2. gather evidence\n3. PAUSE FOR APPROVAL before shipping\n4. attach test output"

    proposal = evo.propose_evolution(db, tid, ask=ask)
    assert proposal["model_used"] is True
    assert "attach test output" in proposal["proposed_body"]


def test_apply_evolution_creates_versioned_provenance() -> None:
    db = FakeDb()
    tid = _template(db)
    for _ in range(3):
        _run(db, tid, deviation="d", deviation_approved=True)
    proposal = evo.propose_evolution(db, tid)
    applied = evo.apply_evolution(
        db, tid, body=proposal["proposed_body"], rationale=proposal["rationale"],
        based_on_instances=proposal["instance_count"],
    )
    assert applied["version"] == 2
    assert applied["provenance"]["kind"] == "evolution"
    assert applied["provenance"]["based_on_instances"] == 3
    # An instance that was already running keeps its original (v1) body.
    assert tmpl.get_template(db, tid, version=1)["body"] == "1. plan\n2. PAUSE FOR APPROVAL"


def test_analyze_unknown_template() -> None:
    assert evo.analyze_template_evolution(FakeDb(), "nope")["ok"] is False
