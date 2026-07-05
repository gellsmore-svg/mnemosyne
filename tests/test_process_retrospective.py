"""Process retrospectives, usage metrics, and similar-task history."""

from __future__ import annotations

from tests.process_fakes import FakeDb
from tirzah.process import enforcement as enf
from tirzah.process import instances as inst
from tirzah.process import retrospective as retro
from tirzah.process import templates as tmpl


def _run(db, *, task, body="1. do", outcome=None, gate=False, deviation=False, override=False):
    t = tmpl.create_template(db, name="P", body=body, risk_level="low")
    instance = inst.start_instance(db, template_id=t["template_id"], task=task, session_id="s")
    iid = instance["instance_id"]
    if gate:
        enf.reach_gate(db, iid, step_id="3")
        enf.resolve_gate(db, iid, step_id="3", approved=True)
    if deviation:
        enf.flag_deviation(db, iid, description="skipped review")
        enf.resolve_deviation(db, iid, approved=True)
    if override:
        enf.record_override(db, iid, justification="urgent")
    if outcome is not None:
        inst.complete_instance(db, iid, outcome=outcome)
    return iid


def test_retrospective_counts_and_summary() -> None:
    db = FakeDb()
    iid = _run(db, task="Ship login fix", outcome="shipped", gate=True, deviation=True, override=True)
    retrospective = retro.build_retrospective(db, iid)
    assert retrospective["outcome"] == "shipped"
    assert retrospective["counts"]["gates"] == 1
    assert retrospective["counts"]["deviations"] == 1
    assert retrospective["counts"]["overrides"] == 1
    assert "Ship login fix" in retrospective["summary"]
    assert "override" in retrospective["summary"].lower()


def test_retrospective_none_for_unknown() -> None:
    assert retro.build_retrospective(FakeDb(), "nope") is None


def test_usage_metrics_rollup() -> None:
    db = FakeDb()
    _run(db, task="a", outcome="shipped")
    _run(db, task="b", outcome="shipped", deviation=True)
    _run(db, task="c")  # still active, no outcome
    inst.abandon_instance(db, _run(db, task="d"), reason="descoped")

    metrics = retro.usage_metrics(db)
    assert metrics["total_instances"] == 4
    assert metrics["completed"] == 2
    assert metrics["abandoned"] == 1
    assert metrics["instances_with_deviations"] == 1
    assert metrics["deviation_rate"] == 0.25
    assert metrics["outcomes"] == {"shipped": 2}


def test_similar_task_history_ranks_by_overlap() -> None:
    db = FakeDb()
    _run(db, task="Fix the login bug", outcome="shipped")
    _run(db, task="Fix the logout bug", outcome="shipped", deviation=True)
    _run(db, task="Write documentation", outcome="done")

    similar = retro.similar_task_history(db, task="Fix the login page bug")
    tasks = [row["task"] for row in similar]
    # Both 'Fix … bug' tasks match; the unrelated doc task is excluded.
    assert "Write documentation" not in tasks
    assert tasks[0] in ("Fix the login bug", "Fix the logout bug")
    assert all("deviations" in row for row in similar)
