"""Outcomes-validation loop — phase 1 (pure engine)."""

from __future__ import annotations

import pytest

from tests.process_fakes import FakeDb
from tirzah.process import instances as inst
from tirzah.process import outcomes as oc
from tirzah.process import templates as tmpl

_OUTCOMES = [
    {"id": "O1", "statement": "The answer cites the fatigue dataset.",
     "check": "fatigue dataset named in evidence"},
    {"id": "O2", "statement": "Order-effect magnitude is framed as a lens."},
]


# --- normalisation ---------------------------------------------------------


def test_normalize_outcomes_assigns_ids_and_accepts_strings() -> None:
    result = oc.normalize_outcomes(["ship the fix", {"statement": "add a test"}])
    assert result == [
        {"id": "O1", "statement": "ship the fix"},
        {"id": "O2", "statement": "add a test"},
    ]
    assert oc.normalize_outcomes(None) == []


def test_normalize_outcomes_rejects_empty_statement() -> None:
    with pytest.raises(ValueError, match="non-empty statement"):
        oc.normalize_outcomes([{"check": "x"}])


def test_normalize_loop_defaults_and_validation() -> None:
    assert oc.normalize_outcomes_loop(None) is None
    loop = oc.normalize_outcomes_loop({})
    assert loop == {
        "cadence": "every_revision", "n": 2,
        "drift_threshold": oc.DEFAULT_DRIFT_THRESHOLD, "on_drift": "reanchor_then_gate",
    }
    with pytest.raises(ValueError, match="cadence"):
        oc.normalize_outcomes_loop({"cadence": "hourly"})
    with pytest.raises(ValueError, match="on_drift"):
        oc.normalize_outcomes_loop({"on_drift": "explode"})
    with pytest.raises(ValueError, match="threshold"):
        oc.normalize_outcomes_loop({"drift_threshold": 5})


# --- template / instance freezing ------------------------------------------


def test_template_stores_and_instance_freezes_outcomes() -> None:
    db = FakeDb()
    t = tmpl.create_template(
        db, name="Anchored", body="1. do the work\n2. validate against outcomes",
        outcomes=_OUTCOMES, outcomes_loop={"drift_threshold": 0.5},
    )
    assert t["outcomes"][0]["id"] == "O1"
    assert t["outcomes_loop"]["drift_threshold"] == 0.5

    instance = inst.start_instance(db, template_id=t["template_id"], task="analyse fatigue")
    assert [o["id"] for o in instance["process_outcomes"]] == ["O1", "O2"]
    assert instance["outcomes_loop"]["drift_threshold"] == 0.5


def test_revise_carries_outcomes_forward() -> None:
    db = FakeDb()
    t = tmpl.create_template(db, name="A", body="do", outcomes=_OUTCOMES)
    revised = tmpl.revise_template(db, t["template_id"], body="do better")
    assert [o["id"] for o in revised["outcomes"]] == ["O1", "O2"]  # carried forward
    replaced = tmpl.revise_template(db, t["template_id"], outcomes=[{"statement": "new"}])
    assert [o["id"] for o in replaced["outcomes"]] == ["O1"]


def test_backward_compatible_without_outcomes() -> None:
    db = FakeDb()
    t = tmpl.create_template(db, name="Plain", body="just do it")
    assert t["outcomes"] == [] and t["outcomes_loop"] is None
    instance = inst.start_instance(db, template_id=t["template_id"], task="x")
    result = oc.validate_outcomes(instance, {"answer": "anything"})
    assert result["ready"] is False and result["drifting"] is False


# --- validation ------------------------------------------------------------


def _instance_with(outcomes, threshold=0.34) -> dict:
    return {"process_outcomes": outcomes, "outcomes_loop": {"drift_threshold": threshold}}


def test_deterministic_detects_alignment_and_drift() -> None:
    instance = _instance_with(_OUTCOMES)
    aligned = oc.validate_outcomes(
        instance,
        {"answer": "We cite the fatigue dataset named in the evidence; the "
                   "order-effect magnitude is presented as a lens, not a prediction."},
    )
    assert aligned["ready"] is True
    assert aligned["drift_score"] == 0.0 and aligned["drifting"] is False

    drifted = oc.validate_outcomes(
        instance, {"answer": "Here is an unrelated discussion about weather patterns."}
    )
    assert drifted["drift_score"] == 1.0 and drifted["drifting"] is True


def test_model_status_overrides_but_records_deterministic() -> None:
    instance = _instance_with(_OUTCOMES)

    def ask(_prompt: str) -> str:
        # Model says both unmet even though text covers them.
        return '[{"id": "O1", "status": "unmet"}, {"id": "O2", "status": "unmet"}]'

    result = oc.validate_outcomes(
        instance,
        {"answer": "fatigue dataset named in evidence; order-effect magnitude lens"},
        ask=ask,
    )
    assert result["model_used"] is True
    o1 = next(o for o in result["per_outcome"] if o["id"] == "O1")
    assert o1["status"] == "unmet"                 # model status is primary
    assert o1["deterministic_status"] == "met"     # floor is preserved for the gate rule
    assert result["drifting"] is True


def test_bad_model_output_falls_back_to_deterministic() -> None:
    instance = _instance_with(_OUTCOMES)
    result = oc.validate_outcomes(
        instance,
        {"answer": "fatigue dataset named in evidence; order-effect magnitude lens"},
        ask=lambda _p: "not json at all",
    )
    assert result["model_used"] is False
    assert result["drifting"] is False  # deterministic floor stands


def test_reanchor_constraint_names_drifted_outcomes() -> None:
    instance = _instance_with(_OUTCOMES)
    result = oc.validate_outcomes(instance, {"answer": "off topic"})
    constraint = oc.render_reanchor_constraint(result)
    assert "RE-ANCHORING" in constraint
    assert "O1" in constraint and "O2" in constraint
    # No drift → empty constraint.
    aligned = oc.validate_outcomes(
        _instance_with([{"id": "O1", "statement": "weather report"}]),
        {"answer": "a detailed weather report about weather"},
    )
    assert oc.render_reanchor_constraint(aligned) == ""
