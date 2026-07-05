"""Smart process auto-selection — deterministic + LLM re-rank."""

from __future__ import annotations

import json

from tests.process_fakes import FakeDb
from tirzah.process import selection as sel
from tirzah.process import templates as tmpl


def _presets(db) -> None:
    tmpl.seed_presets(db)


def test_urgent_task_suggests_emergency() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(db, task="Prod is down, urgent hotfix needed")
    assert result["ok"] is True
    assert result["suggested_template_name"] == "Emergency"
    assert result["inferred_risk"] == "high"
    assert "urgent" in result["signals"]


def test_low_risk_task_suggests_fluid() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(db, task="Quick docs typo cleanup, small refactor")
    assert result["suggested_template_name"] == "Fluid"
    assert result["inferred_risk"] == "low"


def test_high_risk_nonurgent_suggests_governed() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(db, task="Migrate the production billing database schema")
    assert result["suggested_template_name"] == "Governed"
    assert result["inferred_risk"] == "high"


def test_explicit_risk_and_scope_override_inference() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(db, task="do a thing", risk_level="high", scope="product")
    # Governed is the high-risk product-scope preset.
    assert result["suggested_template_name"] == "Governed"


def test_candidates_ranked_and_capped() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(db, task="ship a production release", top_k=2)
    assert len(result["candidates"]) == 2
    scores = [c["score"] for c in result["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_llm_pick_refines_among_candidates() -> None:
    db = FakeDb()
    _presets(db)
    governed = tmpl.latest_template(db, "preset_governed")["template_id"]

    def ask(_prompt: str) -> str:
        return json.dumps({"template_id": governed})

    result = sel.suggest_process(db, task="review a medium-risk change", ask=ask)
    assert result["method"] == "model"
    assert result["suggested_template_id"] == governed


def test_llm_pick_ignored_when_it_names_a_non_candidate() -> None:
    db = FakeDb()
    _presets(db)
    result = sel.suggest_process(
        db, task="ship a production release", ask=lambda _p: '{"template_id": "made_up"}'
    )
    # Falls back to the deterministic winner.
    assert result["method"] == "deterministic"


def test_no_templates() -> None:
    result = sel.suggest_process(FakeDb(), task="anything")
    assert result["ok"] is False and result["suggested_template_id"] is None
