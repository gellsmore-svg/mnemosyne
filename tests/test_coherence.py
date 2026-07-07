import sys
import types
from types import SimpleNamespace

from tirzah.coherence import (
    CANONICAL_REQUEST,
    CANONICAL_RESULT,
    MilcahClient,
    SpecialistRequest,
    SpecialistResult,
    _default_pipeline,
    detect_specialist_call,
    make_client,
    run_planned_specialist,
    validate_specialist_request,
    validate_specialist_result,
)


def _plan(*tool_lists):
    return SimpleNamespace(
        steps=[SimpleNamespace(action=f"step {i}", allowed_tools=list(t)) for i, t in enumerate(tool_lists)]
    )


class _StubClient:
    def __init__(self):
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        return SpecialistResult(claims=["c"], confidence=0.5, terminal_reason="converged")


def test_detect_specialist_call():
    assert detect_specialist_call(_plan(["retrieval"])) is None
    assert detect_specialist_call(_plan(["retrieval"], ["coherence_check"]))[0] == "coherence"
    # research wins when both present on a step
    assert detect_specialist_call(_plan(["coherence", "counter_framework"]))[0] == "research"


def test_run_planned_specialist_paths():
    # no specialist step -> (None, None)
    assert run_planned_specialist(_plan(["retrieval"]), "q", client=_StubClient()) == (None, None)
    # planned but no client -> (mode, None)
    assert run_planned_specialist(_plan(["coherence"]), "q", client=None) == ("coherence", None)
    # planned + client -> runs with the right mode/query
    stub = _StubClient()
    mode, result = run_planned_specialist(_plan(["coherence_check"]), "is it coherent?", client=stub, session_id="s1")
    assert mode == "coherence" and result.claims == ["c"]
    assert stub.calls[0].query == "is it coherent?" and stub.calls[0].mode == "coherence"
    assert stub.calls[0].session_id == "s1"


def _fake_orchestration():
    return SimpleNamespace(
        reasoning=SimpleNamespace(units=[SimpleNamespace(type="claim", text="X holds under A")]),
        challenge=SimpleNamespace(
            objections=[
                SimpleNamespace(
                    text="A fails when Y",
                    metadata={"research_sources": [{"url": "https://example.test/source"}]},
                )
            ],
            counter_frameworks=[SimpleNamespace(title="Rival R")],
        ),
        metrics=SimpleNamespace(global_coherence=0.72),
        roles={"proposer": "gemma"},
        trace=[{"step": "expand"}],
    )


def test_make_client_gated_by_config():
    assert make_client(SimpleNamespace(milcah_enabled=False)) is None
    client = make_client(
        SimpleNamespace(
            milcah_enabled=True,
            milcah_model="gemma",
            hoglah_db_path="/tmp/hoglah.db",
            hoglah_output_dir="/tmp/hoglah-out",
        )
    )
    assert isinstance(client, MilcahClient) and client.model == "gemma"
    assert client.hoglah_db_path == "/tmp/hoglah.db"
    assert client.hoglah_output_dir == "/tmp/hoglah-out"


def test_live_client_runs_pipeline_and_adapts():
    # Inject a fake Milcah pipeline (the real one drives ingest->extract->orchestrate).
    client = MilcahClient(pipeline=lambda _req: _fake_orchestration())
    result = client.run(SpecialistRequest(query="Is it coherent?"))
    assert validate_specialist_result(result) == []
    assert result.claims == ["X holds under A"]
    assert result.objections == ["A fails when Y"]
    assert result.evidence == ["Rival R"]
    assert result.citations == ["https://example.test/source"]
    assert result.confidence == 0.72


def test_live_client_accepts_milcah_provider_result():
    provider = SpecialistResult(
        claims=["provider claim"],
        objections=["provider objection"],
        evidence=["provider evidence"],
        citations=["https://example.test/provider"],
        confidence=0.61,
        terminal_reason="converged",
        trace_metadata={"mode": "coherence"},
    )
    client = MilcahClient(pipeline=lambda _req: provider)
    result = client.run(SpecialistRequest(query="Is it coherent?"))
    assert validate_specialist_result(result) == []
    assert result == provider


def test_default_pipeline_delegates_to_milcah_provider(monkeypatch):
    calls = {}

    package = types.ModuleType("milcah")
    package.__path__ = []
    orchestration = types.ModuleType("milcah.orchestration")
    specialist = types.ModuleType("milcah.specialist")

    class OrchestrationConfig:
        def __init__(self, default_model="", db_path="", output_dir=""):
            self.default_model = default_model
            self.db_path = db_path
            self.output_dir = output_dir

    class SpecialistConfig:
        def __init__(self, orchestration=None):
            self.orchestration = orchestration

    def run_specialist(payload, config=None):
        calls["payload"] = payload
        calls["model"] = config.orchestration.default_model
        calls["db_path"] = config.orchestration.db_path
        calls["output_dir"] = config.orchestration.output_dir
        return SpecialistResult(claims=["provider"], confidence=0.6)

    orchestration.OrchestrationConfig = OrchestrationConfig
    specialist.SpecialistConfig = SpecialistConfig
    specialist.run_specialist = run_specialist
    monkeypatch.setitem(sys.modules, "milcah", package)
    monkeypatch.setitem(sys.modules, "milcah.orchestration", orchestration)
    monkeypatch.setitem(sys.modules, "milcah.specialist", specialist)

    result = _default_pipeline(
        SpecialistRequest(query="q", mode="research"),
        model="gemma",
        hoglah_db_path="/tmp/hoglah.db",
        hoglah_output_dir="/tmp/hoglah-out",
    )
    assert result.claims == ["provider"]
    assert calls["payload"]["mode"] == "research"
    assert calls["model"] == "gemma"
    assert calls["db_path"] == "/tmp/hoglah.db"
    assert calls["output_dir"] == "/tmp/hoglah-out"


def test_live_client_is_fail_soft():
    # pipeline error -> blocked, conformant
    boom = MilcahClient(pipeline=lambda _req: (_ for _ in ()).throw(RuntimeError("milcah down")))
    blocked = boom.run(SpecialistRequest(query="q"))
    assert validate_specialist_result(blocked) == [] and blocked.terminal_reason == "blocked"
    assert blocked.error == "milcah down"
    assert blocked.error_type == "RuntimeError"
    # empty pipeline -> insufficient_evidence
    empty = MilcahClient(pipeline=lambda _req: None).run(SpecialistRequest(query="q"))
    assert empty.terminal_reason == "insufficient_evidence"


def test_canonical_fixtures_conform():
    assert validate_specialist_request(CANONICAL_REQUEST) == []
    assert validate_specialist_result(CANONICAL_RESULT) == []
    assert validate_specialist_request({"query": "query-only uses default mode"}) == []


def test_request_validation():
    assert validate_specialist_request(SpecialistRequest(query="q", mode="research")) == []
    errors = validate_specialist_request({"query": "", "mode": "wat"})
    assert any("query must be non-empty" in e for e in errors)
    assert any("invalid mode: 'wat'" in e for e in errors)


def test_result_validation_catches_drift():
    assert validate_specialist_result(SpecialistResult(claims=["c"], confidence=0.5)) == []
    errors = validate_specialist_result(
        {
            "claims": "not-a-list",
            "objections": [],
            "evidence": [],
            "citations": [],
            "confidence": 1.5,
            "terminal_reason": "exploded",
            "trace_metadata": {},
        }
    )
    assert any("claims must be a list" in e for e in errors)
    assert any("confidence must be a number in [0, 1]" in e for e in errors)
    assert any("invalid terminal_reason: 'exploded'" in e for e in errors)


def test_a_milcah_style_result_conforms():
    # A plausible Milcah specialist verdict must satisfy the Tirzah-side contract.
    result = SpecialistResult(
        claims=["X holds under A"],
        objections=["A fails when Y"],
        evidence=["dataset D counterexample"],
        citations=["http://example.org"],
        confidence=0.7,
        terminal_reason="max_iterations",
        trace_metadata={"trace_id": "t", "job_id": "j", "iterations": 3},
    )
    assert validate_specialist_result(result) == []
