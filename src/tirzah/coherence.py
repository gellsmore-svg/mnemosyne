"""Tirzah <-> Milcah specialist seam (coherence / counter-framework research).

Milcah is the specialist recursive-coherence and research engine. When a Tirzah
plan derives a coherence-check or research need, it dispatches a
:class:`SpecialistRequest` and expects a :class:`SpecialistResult` back.

This module is the **consumer-side contract** for that seam — the request/result
shapes, a :class:`CoherenceClient` protocol, and validators — so the boundary is
enforced by tests before and after a live Milcah call. It mirrors the shape of
:mod:`tirzah.semantic` (the Mahalath seam): a typed result + validator + canonical
fixture. The default client now delegates to Milcah's provider-side
``milcah.specialist.run_specialist`` entrypoint when ``config.milcah_enabled`` is on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

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
    error: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoherenceClient(Protocol):
    def run(self, request: SpecialistRequest) -> SpecialistResult: ...


REQUEST_FIELDS: tuple[str, ...] = ("query",)
RESULT_FIELDS: tuple[str, ...] = (
    "claims",
    "objections",
    "evidence",
    "citations",
    "confidence",
    "terminal_reason",
    "trace_metadata",
    "error",
    "error_type",
)
REQUIRED_PROVIDER_RESULT_FIELDS = tuple(field for field in RESULT_FIELDS if field not in {"error", "error_type"})


def validate_specialist_request(request: Any) -> list[str]:
    """Conformance errors for a specialist request (empty list = conformant)."""
    data = request.to_dict() if isinstance(request, SpecialistRequest) else request
    if not isinstance(data, dict):
        return ["request must be an object"]
    errors = [f"missing request field: {f}" for f in REQUEST_FIELDS if f not in data]
    if not data.get("query"):
        errors.append("query must be non-empty")
    mode = data.get("mode", "coherence")
    if mode not in SPECIALIST_MODES:
        errors.append(f"invalid mode: {mode!r} (allowed: {sorted(SPECIALIST_MODES)})")
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
    "error": None,
    "error_type": None,
}


# --- live Milcah binding ---------------------------------------------------
# Mirrors tirzah.semantic's MahalathResolver/make_resolver: a real client gated by
# config, lazily importing Milcah, fail-soft when Milcah is absent or errors. The
# pipeline/adapter seams are injectable so the wiring is testable without Milcah.
def _text(unit: Any) -> str:
    return getattr(unit, "text", "") or ""


def _type_value(unit: Any) -> str:
    value = getattr(unit, "type", "")
    return str(getattr(value, "value", value))


def _research_citations(units: list[Any]) -> list[str]:
    citations: list[str] = []
    seen: set[str] = set()
    for unit in units:
        metadata = getattr(unit, "metadata", {}) or {}
        for source in metadata.get("research_sources") or []:
            url = source.get("url") if isinstance(source, dict) else getattr(source, "url", "")
            url = str(url or "").strip()
            if url and url not in seen:
                seen.add(url)
                citations.append(url)
    return citations


def _local_adapt(orchestration: Any) -> SpecialistResult:
    """Duck-typed OrchestrationResult -> SpecialistResult (fallback when milcah.contract
    is unavailable, e.g. in tests). Kept in sync with milcah.contract's adapter."""
    reasoning = getattr(orchestration, "reasoning", None)
    units = list(getattr(reasoning, "units", []) or [])
    claims = [_text(u) for u in units if _type_value(u) == "claim" and _text(u)]
    challenge = getattr(orchestration, "challenge", None)
    objection_units = list(getattr(challenge, "objections", []) or [])
    objections = [_text(o) for o in objection_units if _text(o)]
    counter = getattr(challenge, "counter_frameworks", []) or []
    evidence = [getattr(cf, "title", "") or getattr(cf, "name", "") or _text(cf) or str(cf) for cf in counter]
    counter_units = [u for cf in counter for u in (getattr(cf, "units", []) or [])]
    metrics = getattr(orchestration, "metrics", None)
    try:
        confidence = max(0.0, min(1.0, float(getattr(metrics, "global_coherence", 0.0) or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    trace = list(getattr(orchestration, "trace", []) or [])
    return SpecialistResult(
        claims=claims,
        objections=objections,
        evidence=[e for e in evidence if e],
        citations=_research_citations([*objection_units, *counter_units]),
        confidence=confidence,
        terminal_reason="converged",
        trace_metadata={"trace_steps": len(trace), "roles": dict(getattr(orchestration, "roles", {}) or {})},
    )


def _adapt_orchestration(orchestration: Any) -> SpecialistResult:
    """Prefer Milcah's authoritative adapter; fall back to the local one."""
    try:
        from milcah.contract import specialist_result_from_orchestration

        return SpecialistResult(**specialist_result_from_orchestration(orchestration).to_dict())
    except Exception:
        return _local_adapt(orchestration)


def _default_pipeline(request: SpecialistRequest, *, model: str = "") -> Any:
    """Delegate to Milcah's provider-side specialist runner."""
    from milcah.orchestration import OrchestrationConfig
    from milcah.specialist import SpecialistConfig, run_specialist

    config = SpecialistConfig(orchestration=OrchestrationConfig(default_model=model)) if model else None
    return run_specialist(request.to_dict(), config=config)


def _coerce_provider_result(value: Any) -> SpecialistResult | None:
    if isinstance(value, SpecialistResult):
        return value
    if hasattr(value, "to_dict"):
        data = value.to_dict()
    elif isinstance(value, dict):
        data = value
    else:
        return None
    if not isinstance(data, dict) or any(field not in data for field in REQUIRED_PROVIDER_RESULT_FIELDS):
        return None
    try:
        return SpecialistResult(**{field: data.get(field) for field in RESULT_FIELDS})
    except Exception:
        return None


@dataclass
class MilcahClient:
    """Live Tirzah->Milcah specialist client. Runs Milcah's coherence pipeline and
    adapts the result to :class:`SpecialistResult`. ``pipeline``/``adapt`` are
    injectable (tests); defaults drive Milcah over Hoglah. Fail-soft."""

    model: str = ""
    pipeline: Callable[[SpecialistRequest], Any] | None = None
    adapt: Callable[[Any], SpecialistResult] | None = None

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        pipeline = self.pipeline or (lambda req: _default_pipeline(req, model=self.model))
        adapt = self.adapt or _adapt_orchestration
        try:
            orchestration = pipeline(request)
        except Exception as error:
            return SpecialistResult(
                terminal_reason="blocked",
                error=str(error),
                error_type=type(error).__name__,
            )
        if orchestration is None:
            return SpecialistResult(terminal_reason="insufficient_evidence")
        provider_result = _coerce_provider_result(orchestration)
        if provider_result is not None:
            return provider_result
        try:
            return adapt(orchestration)
        except Exception as error:
            return SpecialistResult(
                terminal_reason="blocked",
                error=str(error),
                error_type=type(error).__name__,
            )


def make_client(config: Any) -> "CoherenceClient | None":
    """A live Milcah client when ``config.milcah_enabled``; else None (no-op seam),
    mirroring :func:`tirzah.semantic.make_resolver`."""
    if not getattr(config, "milcah_enabled", False):
        return None
    return MilcahClient(model=getattr(config, "milcah_model", "") or "")


# --- planner trigger -------------------------------------------------------
# A plan step requests a specialist call by naming one of these in allowed_tools.
COHERENCE_TOOLS = frozenset({"coherence_check", "coherence", "milcah", "specialist"})
RESEARCH_TOOLS = frozenset({"counter_framework", "research_specialist", "milcah_research"})
SPECIALIST_TOOLS = COHERENCE_TOOLS | RESEARCH_TOOLS


def _step_tools(step: Any) -> set[str]:
    tools = step.get("allowed_tools") if isinstance(step, dict) else getattr(step, "allowed_tools", None)
    return {str(t) for t in (tools or [])}


def detect_specialist_call(plan: Any) -> tuple[str, Any] | None:
    """If a plan step requests a specialist tool, return (mode, step); else None.

    Research tools win over coherence when both appear on the same step. The planner
    only emits these when told the tool is available (see process_frontend_request).
    """
    steps = plan.get("steps") if isinstance(plan, dict) else getattr(plan, "steps", None)
    for step in steps or []:
        tools = _step_tools(step)
        if tools & RESEARCH_TOOLS:
            return ("research", step)
        if tools & COHERENCE_TOOLS:
            return ("coherence", step)
    return None


def run_planned_specialist(
    plan: Any, query: str, *, client: "CoherenceClient | None", session_id: str | None = None
) -> tuple[str | None, SpecialistResult | None]:
    """Run a specialist call iff the plan derived one. Returns (mode, result):

    - (None, None): the plan did not request a specialist.
    - (mode, None): it did, but no client is available (Milcah disabled/absent).
    - (mode, result): the specialist ran.
    """
    detected = detect_specialist_call(plan)
    if detected is None:
        return (None, None)
    mode, step = detected
    if client is None:
        return (mode, None)
    action = step.get("action") if isinstance(step, dict) else getattr(step, "action", "")
    request = SpecialistRequest(query=query, mode=mode, context=action or "", session_id=session_id)
    return (mode, client.run(request))
