"""Process instances — binding, lifecycle, and trace."""

from __future__ import annotations

import pytest

from tests.process_fakes import FakeDb
from tirzah.process import instances as inst
from tirzah.process import templates as tmpl


def _template(db) -> dict:
    return tmpl.create_template(db, name="Flow", body="1. step", risk_level="low")


def test_start_freezes_the_process_body_at_bind_time() -> None:
    db = FakeDb()
    t = _template(db)
    started = inst.start_instance(db, template_id=t["template_id"], task="Ship X", session_id="s1")
    assert started["status"] == "active"
    assert started["template_version"] == 1
    assert started["process_body"] == "1. step"
    assert started["trace"][0]["event"] == "process.instance.started"

    # Revising the template does NOT change the running instance's process text.
    tmpl.revise_template(db, t["template_id"], body="1. different step")
    reloaded = inst.get_instance(db, started["instance_id"])
    assert reloaded["process_body"] == "1. step"
    assert reloaded["template_version"] == 1


def test_start_can_pin_a_specific_version() -> None:
    db = FakeDb()
    t = _template(db)
    tmpl.revise_template(db, t["template_id"], body="v2")
    started = inst.start_instance(db, template_id=t["template_id"], task="X", version=1)
    assert started["template_version"] == 1 and started["process_body"] == "1. step"


def test_active_instance_for_session_returns_latest_non_terminal() -> None:
    db = FakeDb()
    t = _template(db)
    inst.start_instance(db, template_id=t["template_id"], task="old", session_id="s1")
    newer = inst.start_instance(db, template_id=t["template_id"], task="new", session_id="s1")
    active = inst.active_instance_for_session(db, "s1")
    assert active["instance_id"] == newer["instance_id"]

    inst.complete_instance(db, newer["instance_id"])
    # The completed one is terminal; the older still-active one is now returned.
    assert inst.active_instance_for_session(db, "s1")["task"] == "old"


def test_record_event_appends_and_can_set_status() -> None:
    db = FakeDb()
    t = _template(db)
    started = inst.start_instance(db, template_id=t["template_id"], task="X", session_id="s1")
    updated = inst.record_event(
        db, started["instance_id"], "process.gate.reached",
        {"step": "3"}, status="awaiting_gate",
    )
    assert updated["status"] == "awaiting_gate"
    assert updated["trace"][-1]["event"] == "process.gate.reached"
    assert updated["trace"][-1]["detail"]["step"] == "3"
    with pytest.raises(ValueError):
        inst.record_event(db, started["instance_id"], "x", status="bogus")


def test_complete_and_abandon_set_terminal_state() -> None:
    db = FakeDb()
    t = _template(db)
    a = inst.start_instance(db, template_id=t["template_id"], task="A")
    done = inst.complete_instance(db, a["instance_id"], outcome="shipped", note="ok")
    assert done["status"] == "completed" and done["outcome"] == "shipped"
    assert done["completed_at"] is not None

    b = inst.start_instance(db, template_id=t["template_id"], task="B")
    stopped = inst.abandon_instance(db, b["instance_id"], reason="descoped")
    assert stopped["status"] == "abandoned"
    assert stopped["trace"][-1]["detail"]["reason"] == "descoped"


def test_start_rejects_unknown_template() -> None:
    db = FakeDb()
    with pytest.raises(ValueError):
        inst.start_instance(db, template_id="nope", task="X")
