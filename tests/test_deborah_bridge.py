"""Deborah bridge: plan conversion, conformance, framed detection."""

from __future__ import annotations

import json
from pathlib import Path

from tirzah.planning.deborah_bridge import (
    compose_estate_dispatch,
    framed_result_to_process_result,
    is_framed_substrate_plan,
    run_framed_plan,
    to_deborah_plan,
    validate_against_deborah,
)
from tirzah.planning.recursive import fallback_plan, plan_from_payload


def test_to_deborah_plan_from_fallback():
    plan = fallback_plan("Research X", plan_id="plan_test", reason="test")
    d = to_deborah_plan(plan)
    assert d["plan_id"] == "plan_test"
    assert d["objective"]
    assert d["request"]
    assert d["on_uncertainty"] == "record"
    assert len(d["steps"]) >= 3
    # derived assumes from tirzah_retrieval
    assumes = d.get("assumes") or []
    assert any("retrieve" in a for a in assumes)
    errors = validate_against_deborah(d, profile="full")
    assert errors == [], errors


def test_to_deborah_plan_maps_awaiting_and_concurrent():
    payload = {
        "objective": "x",
        "status": "active",
        "steps": [
            {
                "id": "1",
                "action": "wait",
                "construct": "CONCURRENT",
                "status": "awaiting",
                "success_criteria": ["done"],
            }
        ],
        "stopping_conditions": ["done"],
        "revision_decision": "stable",
    }
    plan = plan_from_payload(
        payload,
        plan_id="p",
        revision=1,
        parent_revision=None,
        request="x",
        trigger="t",
        max_steps=4,
    )
    # plan_from_payload may rewrite status to pending always
    d = to_deborah_plan(
        {
            **plan.to_dict(),
            "steps": [
                {
                    "id": "1",
                    "action": "wait",
                    "construct": "CONCURRENT",
                    "status": "awaiting",
                    "success_criteria": ["done"],
                    "depends_on": [],
                    "allowed_tools": [],
                }
            ],
        }
    )
    assert d["steps"][0]["construct"] == "PARALLEL"
    assert d["steps"][0]["status"] == "pending"
    assert validate_against_deborah(d) == []


def test_is_framed_substrate_plan_fallback_is_false():
    plan = fallback_plan("Research X", plan_id="p", reason="t")
    assert is_framed_substrate_plan(plan) is False


def test_is_framed_substrate_plan_detects_critique_decision():
    plan = {
        "plan_id": "framed",
        "revision": 1,
        "objective": "Ground claim",
        "status": "active",
        "request": "Is X true?",
        "steps": [
            {
                "id": "1",
                "construct": "CALL",
                "action": "tirzah.retrieve — gather",
                "status": "pending",
                "allowed_tools": ["tirzah_retrieval"],
                "success_criteria": ["evidence"],
            },
            {
                "id": "2",
                "construct": "CALL",
                "action": "milcah.critique — pressure test",
                "status": "pending",
                "allowed_tools": ["milcah"],
                "success_criteria": ["scores"],
            },
            {
                "id": "3",
                "construct": "DECISION",
                "action": "Commit accept or open",
                "status": "pending",
                "success_criteria": ["selected"],
                "cognition": "decide",
            },
        ],
        "stopping_conditions": ["verdict"],
        "revision_decision": "stable",
        "assumes": ["tirzah.retrieve@1", "milcah.critique@1"],
    }
    assert is_framed_substrate_plan(plan) is True


def test_compose_estate_dispatch_includes_retrieve():
    d = compose_estate_dispatch(search=lambda q, limit=10: [])
    assert "tirzah.retrieve" in d or "retrieve" in d


def test_run_framed_plan_offline_demo_path():
    """Framed run with demo estate (no Mongo) should not crash."""
    plan = {
        "plan_id": "framed_demo",
        "revision": 1,
        "objective": "Ground claim",
        "status": "active",
        "request": "Is relational substrate coherence well-supported?",
        "intent": "Ground a claim with critique",
        "assumes": [
            "tirzah.retrieve@1",
            "deborah.infer@1",
            "milcah.critique@1",
        ],
        "on_uncertainty": "record",
        "steps": [
            {
                "id": "s1",
                "construct": "STEP",
                "action": "Retrieve evidence",
                "status": "pending",
                "cognition": "observe",
                "allowed_tools": ["tirzah_retrieval"],
                "success_criteria": ["evidence list"],
            },
            {
                "id": "s2",
                "construct": "STEP",
                "action": "Form reading",
                "status": "pending",
                "cognition": "infer",
                "success_criteria": ["claim"],
            },
            {
                "id": "s3",
                "construct": "CALL",
                "action": "milcah.critique — pressure test",
                "status": "pending",
                "cognition": "evaluate",
                "allowed_tools": ["milcah"],
                "success_criteria": ["scores"],
            },
            {
                "id": "s4",
                "construct": "DECISION",
                "action": "Commit",
                "status": "pending",
                "cognition": "decide",
                "success_criteria": ["selected"],
            },
        ],
        "stopping_conditions": ["verdict or open"],
        "revision_decision": "stable",
    }
    assert is_framed_substrate_plan(plan)
    # Inject empty search so retrieve never hits real Mongo/Hoglah
    framed = run_framed_plan(
        plan,
        db=None,
        search=lambda q, limit=10: [],
        negotiate=True,
        negotiator_name="accept",
        decisions={"default": "open"},
    )
    assert framed["framed"] is True
    assert framed["terminal"] in {"complete", "open", "refused", "blocked"}
    assert framed.get("slice") is not None or framed.get("error")
    proc = framed_result_to_process_result(framed, query=plan["request"], session_id="s1")
    assert proc["framed_execution"] is True
    assert "process_trace" in proc


def test_deborah_fixture_interop_still_validates():
    """Deborah's frozen Tirzah fallback fixture (if present) validates both ways."""
    root = Path(__file__).resolve().parents[2]  # domains/Tirzah
    fixture = root.parent / "Deborah" / "tests" / "fixtures" / "tirzah_fallback_plan.json"
    if not fixture.exists():
        return
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert validate_against_deborah(data) == []
    # round-trip through to_deborah_plan
    assert validate_against_deborah(to_deborah_plan(data)) == []
