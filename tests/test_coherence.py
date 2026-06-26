from tirzah.coherence import (
    CANONICAL_REQUEST,
    CANONICAL_RESULT,
    SpecialistRequest,
    SpecialistResult,
    validate_specialist_request,
    validate_specialist_result,
)


def test_canonical_fixtures_conform():
    assert validate_specialist_request(CANONICAL_REQUEST) == []
    assert validate_specialist_result(CANONICAL_RESULT) == []


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
