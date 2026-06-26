"""Tirzah <-> Milcah specialist seam (coherence / counter-framework research).

Milcah is the specialist recursive-coherence and research engine. When a Tirzah
plan derives a coherence-check or research need, it dispatches a
:class:`SpecialistRequest` and expects a :class:`SpecialistResult` back.

This module is the **consumer-side contract** for that seam — the request/result
shapes, a :class:`CoherenceClient` protocol, and validators — so the boundary is
enforced by tests before (and after) a live Milcah call is wired. It mirrors the
shape of :mod:`tirzah.semantic` (the Mahalath seam): a typed result + validator +
canonical fixture. Milcah should expose a matching provider-side contract; the live
client binding is a follow-up gated by ``config.milcah_enabled``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

# A specialist call is either a coherence pressure-test or counter-framework research.
SPECIALIST_MODES = frozenset({"coherence", "research"})
# Why the specialist loop stopped (so the caller can reason about completeness).
TERMINAL_REASONS = frozenset(
    {"converged", "max_iterations", "no_objections", "insufficient_evidence", "blocked"}
)


@dataclass
class SpecialistRequest:
    """What Tirzah sends Milcah for a specialist call."""

    query: str
    mode: str = "coherence"  # one of SPECIALIST_MODES
    context: str = ""
    max_iterations: int = 3
    trace_id: str | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialistResult:
    """What Milcah returns: a bounded, evidenced coherence/research verdict."""

    claims: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    confidence: float = 0.0  # in [0, 1]
    terminal_reason: str = "converged"  # one of TERMINAL_REASONS
    trace_metadata: dict[str, Any] = field(default_factory=dict)  # trace_id/job_id/iterations

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoherenceClient(Protocol):
    def run(self, request: SpecialistRequest) -> SpecialistResult: ...


REQUEST_FIELDS: tuple[str, ...] = ("query", "mode")
RESULT_FIELDS: tuple[str, ...] = (
    "claims",
    "objections",
    "evidence",
    "citations",
    "confidence",
    "terminal_reason",
    "trace_metadata",
)


def validate_specialist_request(request: Any) -> list[str]:
    """Conformance errors for a specialist request (empty list = conformant)."""
    data = request.to_dict() if isinstance(request, SpecialistRequest) else request
    if not isinstance(data, dict):
        return ["request must be an object"]
    errors = [f"missing request field: {f}" for f in REQUEST_FIELDS if f not in data]
    if not data.get("query"):
        errors.append("query must be non-empty")
    if data.get("mode") not in SPECIALIST_MODES:
        errors.append(f"invalid mode: {data.get('mode')!r} (allowed: {sorted(SPECIALIST_MODES)})")
    return errors


def validate_specialist_result(result: Any) -> list[str]:
    """Conformance errors for a specialist result (empty list = conformant)."""
    data = result.to_dict() if isinstance(result, SpecialistResult) else result
    if not isinstance(data, dict):
        return ["result must be an object"]
    errors = [f"missing result field: {f}" for f in RESULT_FIELDS if f not in data]
    for list_field in ("claims", "objections", "evidence", "citations"):
        if list_field in data and not isinstance(data[list_field], list):
            errors.append(f"{list_field} must be a list")
    confidence = data.get("confidence")
    if confidence is not None and not (isinstance(confidence, (int, float)) and 0.0 <= float(confidence) <= 1.0):
        errors.append("confidence must be a number in [0, 1]")
    reason = data.get("terminal_reason")
    if reason is not None and reason not in TERMINAL_REASONS:
        errors.append(f"invalid terminal_reason: {reason!r} (allowed: {sorted(TERMINAL_REASONS)})")
    return errors


# Executable fixtures — known-conformant request/result for both-side tests.
CANONICAL_REQUEST: dict[str, Any] = {
    "query": "Is the proposed framework internally coherent?",
    "mode": "coherence",
    "context": "",
    "max_iterations": 3,
    "trace_id": "trace_abc",
    "session_id": "s1",
}

CANONICAL_RESULT: dict[str, Any] = {
    "claims": ["The framework is internally consistent under assumption A."],
    "objections": ["Assumption A is unsupported when condition X holds."],
    "evidence": ["Counterexample observed in dataset D."],
    "citations": ["https://example.org/source"],
    "confidence": 0.62,
    "terminal_reason": "converged",
    "trace_metadata": {"trace_id": "trace_abc", "iterations": 3},
}
