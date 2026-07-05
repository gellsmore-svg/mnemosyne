"""Process management REST routes on `tirzah serve` (hermetic, fake Mongo)."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tests.process_fakes import FakeDb  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    import tirzah.web.app as webapp

    fake = FakeDb()
    monkeypatch.setattr(webapp, "get_database", lambda _mongo: fake)
    monkeypatch.setattr(webapp, "ensure_indexes", lambda _db: None)
    app = webapp.create_app()
    # No `with` → lifespan background threads don't start; routes are live.
    return TestClient(app)


def test_presets_seeded_on_serve(client) -> None:
    rows = client.get("/api/process/templates").json()["templates"]
    names = {t["name"] for t in rows}
    assert {"Governed", "Fluid", "Emergency"} <= names


def test_template_create_revise_and_versions(client) -> None:
    created = client.post("/api/process/templates", json={
        "name": "Custom", "body": "1. do it", "risk_level": "medium",
    }).json()["template"]
    tid = created["template_id"]
    assert created["version"] == 1

    revised = client.post(f"/api/process/templates/{tid}/revise", json={"body": "1. do it better"}).json()["template"]
    assert revised["version"] == 2

    detail = client.get(f"/api/process/templates/{tid}").json()
    assert detail["template"]["body"] == "1. do it better"
    assert [v["version"] for v in detail["versions"]] == [1, 2]

    bad = client.post("/api/process/templates", json={"name": " ", "body": "x"})
    assert bad.status_code == 400


def test_instance_lifecycle_gate_and_retrospective(client) -> None:
    # Start an instance under the Governed preset for a session.
    instance = client.post("/api/process/instances", json={
        "template_id": "preset_governed", "task": "Ship the auth fix", "session_id": "sess-1",
    }).json()["instance"]
    iid = instance["instance_id"]
    assert instance["status"] == "active"
    assert "PAUSE FOR APPROVAL" in instance["process_body"]

    # It's the active process for the session.
    active = client.get("/api/process/active", params={"session_id": "sess-1"}).json()["instance"]
    assert active["instance_id"] == iid

    # Flag a deviation → pauses; resolve it → resumes.
    client.post(f"/api/process/instances/{iid}/deviation", json={"description": "skipping a gate"})
    assert client.get(f"/api/process/instances/{iid}").json()["instance"]["status"] == "awaiting_gate"
    client.post(f"/api/process/instances/{iid}/deviation/resolve", json={"approved": True})
    assert client.get(f"/api/process/instances/{iid}").json()["instance"]["status"] == "active"

    # Complete and read the retrospective + metrics.
    client.post(f"/api/process/instances/{iid}/complete", json={"outcome": "shipped"})
    retro = client.get(f"/api/process/instances/{iid}/retrospective").json()["retrospective"]
    assert retro["outcome"] == "shipped"
    assert retro["counts"]["deviations"] == 1

    metrics = client.get("/api/process/metrics").json()["metrics"]
    assert metrics["completed"] == 1
    assert metrics["outcomes"] == {"shipped": 1}


def test_override_requires_justification(client) -> None:
    instance = client.post("/api/process/instances", json={
        "template_id": "preset_emergency", "task": "Prod down", "session_id": "sess-2",
    }).json()["instance"]
    iid = instance["instance_id"]
    assert client.post(f"/api/process/instances/{iid}/override", json={"justification": "  "}).status_code == 400
    ok = client.post(f"/api/process/instances/{iid}/override", json={"justification": "db is down"})
    assert ok.json()["instance"]["trace"][-1]["event"] == "process.override.invoked"


def test_history_query(client) -> None:
    for task, outcome in [("Fix login bug", "shipped"), ("Fix logout bug", "shipped"), ("Write docs", "done")]:
        inst = client.post("/api/process/instances", json={
            "template_id": "preset_fluid", "task": task,
        }).json()["instance"]
        client.post(f"/api/process/instances/{inst['instance_id']}/complete", json={"outcome": outcome})

    history = client.get("/api/process/history", params={"task": "Fix the login page bug"}).json()["history"]
    tasks = [row["task"] for row in history]
    assert "Write docs" not in tasks
    assert any("login" in t for t in tasks)


def test_review_and_suggest_and_trial_routes(client) -> None:
    # Structural review (no model) flags a missing gate.
    review = client.post("/api/process/review", json={
        "body": "do the thing", "use_model": False,
    }).json()["review"]
    assert any(f["kind"] == "missing_gate" for f in review["findings"])

    # Auto-suggest picks Emergency for an urgent task.
    suggestion = client.post("/api/process/suggest", json={
        "task": "Prod is down — urgent hotfix",
    }).json()["suggestion"]
    assert suggestion["suggested_template_name"] == "Emergency"
    assert "urgent" in suggestion["signals"]

    # Trial run returns a gate-match verdict (planner may be mock/stub).
    trial = client.post("/api/process/trial", json={
        "body": "1. plan\n2. PAUSE FOR APPROVAL before ship",
        "sample_task": "Ship a change",
    }).json()["trial"]
    assert "gate_expected" in trial and trial["gate_expected"] is True
