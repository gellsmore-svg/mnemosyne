"""Tirzah-assisted process authoring — review and trial run."""

from __future__ import annotations

import json

from tirzah.process import refinement as ref


def test_structural_review_flags_missing_gate_and_short_body() -> None:
    result = ref.review_process("do it")  # no ask → structural only
    kinds = {f["kind"] for f in result["findings"]}
    assert "gap" in kinds  # very short
    assert "missing_gate" in kinds  # no gate stated
    assert result["has_gates"] is False
    assert result["model_used"] is False


def test_structural_review_recognises_a_gated_process() -> None:
    body = (
        "1. Understand the request.\n"
        "2. Gather evidence.\n"
        "3. PAUSE FOR APPROVAL before shipping.\n"
    )
    result = ref.review_process(body)
    assert result["has_gates"] is True
    kinds = {f["kind"] for f in result["findings"]}
    assert "missing_gate" not in kinds  # gate is present
    assert "ambiguity" not in kinds  # numbered steps


def test_model_review_merges_questions_and_suggestion() -> None:
    def ask(_prompt: str) -> str:
        return json.dumps({
            "clarifying_questions": ["Who approves at the gate?", ""],
            "findings": [{"kind": "ambiguity", "note": "Step 2 is vague."}],
            "suggested_body": "1. plan\n2. PAUSE FOR APPROVAL\n",
        })

    result = ref.review_process("1. do stuff\n2. PAUSE FOR APPROVAL", ask=ask)
    assert result["model_used"] is True
    assert result["clarifying_questions"] == ["Who approves at the gate?"]
    assert any(f["source"] == "model" and f["kind"] == "ambiguity" for f in result["findings"])
    assert result["suggested_body"].startswith("1. plan")


def test_model_review_survives_garbage_output() -> None:
    result = ref.review_process("1. a\n2. PAUSE FOR APPROVAL", ask=lambda _p: "not json at all")
    assert result["ok"] is True
    assert result["model_used"] is False  # fell back cleanly
    # Structural findings still present.
    assert isinstance(result["findings"], list)


class _FakePlanStep:
    def __init__(self, id, construct, action="a", allowed_tools=None):
        self.id = id
        self.construct = construct
        self.action = action
        self.allowed_tools = allowed_tools or []


class _FakePlan:
    plan_id = "plan_trial"
    objective = "obj"

    def __init__(self, steps):
        self.steps = steps


def test_trial_reports_gate_match(monkeypatch) -> None:
    import tirzah.process.refinement as refinement

    # Gated process + a plan that includes an AWAIT step → matches.
    gated_plan = _FakePlan([
        _FakePlanStep("1", "STEP"),
        _FakePlanStep("2", "AWAIT"),
        _FakePlanStep("3", "CALL", allowed_tools=["answer_adapter"]),
    ])
    captured = {}

    def fake_create(task, *, planner, max_steps, context):
        captured["context"] = context
        return gated_plan

    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan", fake_create
    )
    monkeypatch.setattr("tirzah.planning.recursive.make_planner", lambda runtime: (lambda p: "{}"))

    class Cfg:
        class runtime:
            planning_max_steps = 8

    result = refinement.trial_process(
        db=None, config=Cfg, body="1. plan\n2. PAUSE FOR APPROVAL before ship",
        sample_task="Ship a fix",
    )
    assert result["ok"] is True
    assert result["has_gate_steps"] is True
    assert result["gate_expected"] is True
    assert result["plan_matches_gates"] is True
    # The process text conditioned the trial plan.
    assert "ACTIVE PROCESS" in captured["context"]


def test_trial_flags_missing_gate(monkeypatch) -> None:
    import tirzah.process.refinement as refinement

    # Gated process but a plan WITHOUT an AWAIT step → mismatch (the process
    # asked for a gate the plan didn't place).
    plain_plan = _FakePlan([_FakePlanStep("1", "STEP"), _FakePlanStep("2", "CALL")])
    monkeypatch.setattr(
        "tirzah.planning.recursive.create_initial_plan",
        lambda task, *, planner, max_steps, context: plain_plan,
    )
    monkeypatch.setattr("tirzah.planning.recursive.make_planner", lambda runtime: (lambda p: "{}"))

    class Cfg:
        class runtime:
            planning_max_steps = 8

    result = refinement.trial_process(
        db=None, config=Cfg, body="1. plan\n2. PAUSE FOR APPROVAL",
        sample_task="Ship",
    )
    assert result["gate_expected"] is True
    assert result["has_gate_steps"] is False
    assert result["plan_matches_gates"] is False
