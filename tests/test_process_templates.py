"""Process templates — versioned storage + preset seeding."""

from __future__ import annotations

import pytest

from tests.process_fakes import FakeDb
from tirzah.process import templates as tmpl


def test_create_and_latest_roundtrip() -> None:
    db = FakeDb()
    created = tmpl.create_template(
        db, name="My Flow", body="1. do the thing", description="a flow",
        category="feature", risk_level="medium", scope="feature",
    )
    assert created["version"] == 1
    assert created["name"] == "My Flow"
    assert created["is_preset"] is False
    latest = tmpl.latest_template(db, created["template_id"])
    assert latest["body"] == "1. do the thing"


def test_revise_appends_version_and_preserves_history() -> None:
    db = FakeDb()
    v1 = tmpl.create_template(db, name="Flow", body="v1 body", description="d")
    v2 = tmpl.revise_template(db, v1["template_id"], body="v2 body")
    assert v2["version"] == 2
    # Unspecified fields carry forward.
    assert v2["name"] == "Flow" and v2["description"] == "d"
    # History is preserved: both versions retrievable.
    assert tmpl.get_template(db, v1["template_id"], version=1)["body"] == "v1 body"
    assert tmpl.get_template(db, v1["template_id"])["body"] == "v2 body"
    assert [v["version"] for v in tmpl.template_versions(db, v1["template_id"])] == [1, 2]


def test_validation() -> None:
    db = FakeDb()
    with pytest.raises(ValueError):
        tmpl.create_template(db, name=" ", body="x")
    with pytest.raises(ValueError):
        tmpl.create_template(db, name="ok", body=" ")
    with pytest.raises(ValueError):
        tmpl.create_template(db, name="ok", body="b", risk_level="extreme")
    with pytest.raises(ValueError):
        tmpl.revise_template(db, "nope", body="b")


def test_list_filters_and_latest_only() -> None:
    db = FakeDb()
    a = tmpl.create_template(db, name="A", body="b", category="bug", risk_level="low")
    tmpl.revise_template(db, a["template_id"], body="b2")  # A now v2
    tmpl.create_template(db, name="B", body="b", category="feature", risk_level="high")

    rows = tmpl.list_templates(db)
    assert len(rows) == 2  # one row per template (latest version), not per version
    a_row = next(r for r in rows if r["name"] == "A")
    assert a_row["version"] == 2
    assert [r["name"] for r in tmpl.list_templates(db, category="bug")] == ["A"]
    assert [r["name"] for r in tmpl.list_templates(db, risk_level="high")] == ["B"]


def test_seed_presets_is_idempotent() -> None:
    db = FakeDb()
    first = tmpl.seed_presets(db)
    names = {p["name"] for p in first}
    assert names == {"Governed", "Fluid", "Emergency"}
    assert all(p["is_preset"] for p in first)
    # Second call creates nothing new.
    assert tmpl.seed_presets(db) == []
    assert len(tmpl.list_templates(db)) == 3
    # Presets can be excluded.
    assert tmpl.list_templates(db, include_presets=False) == []


def test_preset_bodies_state_gates() -> None:
    db = FakeDb()
    tmpl.seed_presets(db)
    governed = tmpl.latest_template(db, "preset_governed")
    assert "PAUSE FOR APPROVAL" in governed["body"]
    emergency = tmpl.latest_template(db, "preset_emergency")
    assert "MANDATORY RETROSPECTIVE" in emergency["body"]
