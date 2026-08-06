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

from dataclasses import dataclass
from typing import Any, Callable, Protocol

# The request/result shapes and their vocabularies live in Keturah — the same
# contract used to be written out here *and* in milcah.contract, and the two
# copies had already drifted (only this side carried error/error_type).
# Re-exported so this module's public surface is unchanged for callers.
from keturah import (  # noqa: F401 — re-exported as this module's public API
    REQUEST_FIELDS,
    RESULT_FIELDS,
    SPECIALIST_MODES,
    TERMINAL_REASONS,
    Evidence,
    SpecialistRequest,
    SpecialistResult,
    normalise_evidence,
)
from keturah import validate_request as validate_specialist_request  # noqa: F401
from keturah import validate_result as validate_specialist_result  # noqa: F401

class CoherenceClient(Protocol):
    def run(self, request: SpecialistRequest) -> SpecialistResult: ...


# Tirzah-specific: what a *provider* must return. error/error_type are the
# caller's to set when the call itself fails, so they are not required of the
# provider — the rest of the shared result shape is.
REQUIRED_PROVIDER_RESULT_FIELDS = tuple(
    name for name in RESULT_FIELDS if name not in {"error", "error_type"}
)

# Canonical fixtures — the shapes the seam is tested against on both sides.
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


def _default_pipeline(
    request: SpecialistRequest,
    *,
    model: str = "",
    hoglah_db_path: str = "",
    hoglah_output_dir: str = "",
    hoglah_transport: str = "store",
    hoglah_timeout_seconds: int | None = None,
) -> Any:
    """Delegate to Milcah's provider-side specialist runner."""
    from milcah.orchestration import OrchestrationConfig
    from milcah.specialist import SpecialistConfig, run_specialist

    orchestration_kwargs: dict[str, Any] = {}
    if model:
        orchestration_kwargs["default_model"] = model
    if hoglah_db_path:
        orchestration_kwargs["db_path"] = str(hoglah_db_path)
    if hoglah_output_dir:
        orchestration_kwargs["output_dir"] = str(hoglah_output_dir)
    if hoglah_transport and hoglah_transport != "store":
        orchestration_kwargs["transport"] = str(hoglah_transport)
    if hoglah_timeout_seconds is not None:
        orchestration_kwargs["timeout"] = float(hoglah_timeout_seconds)
    config = SpecialistConfig(orchestration=OrchestrationConfig(**orchestration_kwargs))
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
    hoglah_db_path: str = ""
    hoglah_output_dir: str = ""
    hoglah_transport: str = "store"
    hoglah_timeout_seconds: int | None = None
    pipeline: Callable[[SpecialistRequest], Any] | None = None
    adapt: Callable[[Any], SpecialistResult] | None = None

    def run(self, request: SpecialistRequest) -> SpecialistResult:
        pipeline = self.pipeline or (
            lambda req: _default_pipeline(
                req,
                model=self.model,
                hoglah_db_path=self.hoglah_db_path,
                hoglah_output_dir=self.hoglah_output_dir,
                hoglah_transport=self.hoglah_transport,
                hoglah_timeout_seconds=self.hoglah_timeout_seconds,
            )
        )
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
    return MilcahClient(
        model=getattr(config, "milcah_model", "") or "",
        hoglah_db_path=str(getattr(config, "hoglah_db_path", "") or ""),
        hoglah_output_dir=str(getattr(config, "hoglah_output_dir", "") or ""),
        hoglah_transport=str(getattr(config, "hoglah_transport", "store") or "store"),
        hoglah_timeout_seconds=getattr(config, "hoglah_wait_timeout_seconds", None),
    )


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
