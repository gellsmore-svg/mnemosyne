"""Deborah ↔ Tirzah plan handoff (validate, framed substrate execution).

Ownership (see Deborah ``docs/PLAN-OWNERSHIP.md``):

- **Tirzah** authors/revises plans and runs the rich interpretive executor.
- **Deborah** owns PLAN conformance and crystallised *framed* walks
  (negotiate → post-retrieve gate → residual/open question).

This module is the only seam that should call ``deborah.runtime.run_substrate_slice``
from Tirzah.
"""

from __future__ import annotations

from typing import Any

# Tirzah tool names → Deborah capability stems (ASSUMES / CALL).
_TOOL_TO_STEM: dict[str, str] = {
    "tirzah_retrieval": "tirzah.retrieve",
    "search_nodes": "tirzah.retrieve",
    "retrieve": "tirzah.retrieve",
    "milcah": "milcah.critique",
    "coherence": "milcah.critique",
    "coherence_check": "milcah.critique",
    "specialist": "milcah.critique",
    "counter_framework": "milcah.critique",
    "research_specialist": "milcah.critique",
    "milcah_research": "milcah.critique",
    "mahalath": "mahalath.detect_novel",
    "detect_novel": "mahalath.detect_novel",
}

_STATUS_MAP = {
    "awaiting": "pending",  # Deborah STEP_STATUSES has no awaiting
}

_CONSTRUCT_MAP = {
    "CONCURRENT": "PARALLEL",  # Deborah has PARALLEL, not CONCURRENT
}

# Markers that a plan is a crystallised substrate / critique graph.
_FRAMED_ASSUME_MARKERS = (
    "milcah.critique",
    "milcah.validate_against_intent",
    "milcah.assess_confidence",
    "mahalath.detect_novel",
    "deborah.infer",
)
_FRAMED_BLOB_MARKERS = (
    "milcah.critique",
    "validate_against_intent",
    "assess_confidence",
    "detect_novel",
    "deborah.infer",
    "coherence_check",
)


def to_deborah_plan(plan: Any) -> dict[str, Any]:
    """Convert a Tirzah ``CairnPlan`` or plan dict into Deborah ``validate_plan`` shape.

    Normalises Tirzah-only statuses/constructs and derives ``assumes`` from
    allowed_tools when framing fields are absent.
    """
    if plan is None:
        return {}
    if hasattr(plan, "to_dict") and callable(plan.to_dict):
        raw = plan.to_dict()
    elif isinstance(plan, dict):
        raw = dict(plan)
    else:
        raise TypeError(f"unsupported plan type: {type(plan)!r}")

    out: dict[str, Any] = {
        "plan_id": str(raw.get("plan_id") or "unknown"),
        "revision": int(raw.get("revision") or 1),
        "parent_revision": raw.get("parent_revision"),
        "request": str(raw.get("request") or raw.get("objective") or ""),
        "trigger": str(raw.get("trigger") or "initial_request"),
        "objective": str(raw.get("objective") or raw.get("request") or ""),
        "status": str(raw.get("status") or "active"),
        "stopping_conditions": list(raw.get("stopping_conditions") or []),
        "unresolved_questions": list(raw.get("unresolved_questions") or []),
        "revision_decision": str(raw.get("revision_decision") or "revise"),
        "revision_reason": str(raw.get("revision_reason") or ""),
    }
    # Framing (optional on Tirzah plans)
    intent = raw.get("intent") or raw.get("objective") or raw.get("request")
    if intent:
        out["intent"] = str(intent)
    if raw.get("outcomes") is not None:
        out["outcomes"] = list(raw["outcomes"])
    elif out["stopping_conditions"]:
        out["outcomes"] = list(out["stopping_conditions"])
    out["on_uncertainty"] = str(raw.get("on_uncertainty") or "record")
    if raw.get("exploration_budget") is not None:
        out["exploration_budget"] = raw["exploration_budget"]
    if raw.get("reflective_pass") is not None:
        out["reflective_pass"] = raw["reflective_pass"]

    steps_in = raw.get("steps") or []
    steps_out: list[dict[str, Any]] = []
    derived_assumes: list[str] = []
    for step in steps_in:
        if not isinstance(step, dict):
            if hasattr(step, "__dict__"):
                step = {
                    "id": getattr(step, "id", ""),
                    "action": getattr(step, "action", ""),
                    "construct": getattr(step, "construct", "STEP"),
                    "status": getattr(step, "status", "pending"),
                    "depends_on": list(getattr(step, "depends_on", []) or []),
                    "success_criteria": list(getattr(step, "success_criteria", []) or []),
                    "allowed_tools": list(getattr(step, "allowed_tools", []) or []),
                    "cognition": getattr(step, "cognition", None),
                }
            else:
                continue
        construct = str(step.get("construct") or "STEP").upper()
        construct = _CONSTRUCT_MAP.get(construct, construct)
        status = str(step.get("status") or "pending").lower()
        status = _STATUS_MAP.get(status, status)
        tools = [str(t) for t in (step.get("allowed_tools") or []) if str(t).strip()]
        for tool in tools:
            stem = _TOOL_TO_STEM.get(tool) or _TOOL_TO_STEM.get(tool.lower())
            if stem and stem not in derived_assumes:
                derived_assumes.append(stem)
            # namespaced tools pass through
            if "." in tool and tool not in derived_assumes:
                derived_assumes.append(tool.split("@", 1)[0])
        row: dict[str, Any] = {
            "id": str(step.get("id") or f"s{len(steps_out)+1}"),
            "action": str(step.get("action") or ""),
            "construct": construct,
            "status": status if status in {
                "pending", "active", "completed", "blocked", "skipped"
            } else "pending",
            "depends_on": list(step.get("depends_on") or []),
            "success_criteria": list(step.get("success_criteria") or []),
            "allowed_tools": tools,
        }
        if step.get("cognition"):
            row["cognition"] = str(step["cognition"]).lower()
        if step.get("output"):
            row["output"] = step["output"]
        steps_out.append(row)
    out["steps"] = steps_out

    assumes = raw.get("assumes")
    if isinstance(assumes, list) and assumes:
        out["assumes"] = [str(a) for a in assumes if str(a).strip()]
    elif derived_assumes:
        out["assumes"] = derived_assumes

    # Drop non-Deborah noise that can confuse validators
    return out


def validate_against_deborah(
    plan: Any,
    *,
    profile: str = "full",
) -> list[str]:
    """Return Deborah ``validate_plan`` errors (empty = conformant)."""
    dplan = to_deborah_plan(plan)
    try:
        import deborah

        return list(deborah.validate_plan(dplan, profile=profile) or [])
    except Exception as exc:  # noqa: BLE001 — fail-soft at estate boundary
        return [f"deborah.validate_plan unavailable: {type(exc).__name__}: {exc}"]


def ensure_deborah_conformance(
    plan: Any,
    *,
    profile: str = "full",
    require: bool = False,
) -> list[str]:
    """Validate and optionally raise when ``require`` is True."""
    errors = validate_against_deborah(plan, profile=profile)
    if errors and require:
        raise ValueError(
            "plan does not conform to Deborah validate_plan: " + "; ".join(errors[:8])
        )
    return errors


def is_framed_substrate_plan(plan: Any) -> bool:
    """True when the plan looks like a crystallised Deborah substrate / critique graph.

    Heuristic: critique/novel/intent evaluate markers **and** a DECISION or
    decide cognition, or explicit ASSUMES listing milcah/mahalath/deborah.infer.
    """
    dplan = to_deborah_plan(plan)
    assumes = " ".join(str(a).lower() for a in (dplan.get("assumes") or []))
    if any(m in assumes for m in _FRAMED_ASSUME_MARKERS):
        # Strong signal from ASSUMES alone if milcah.critique present
        if "milcah.critique" in assumes or "mahalath" in assumes:
            return True
    steps = dplan.get("steps") or []
    blob_parts: list[str] = [assumes]
    has_decision = False
    for s in steps:
        if not isinstance(s, dict):
            continue
        blob_parts.append(str(s.get("action") or "").lower())
        blob_parts.append(" ".join(str(t).lower() for t in (s.get("allowed_tools") or [])))
        if str(s.get("cognition") or "").lower() == "decide":
            has_decision = True
        if str(s.get("construct") or "").upper() == "DECISION":
            has_decision = True
    blob = " ".join(blob_parts)
    has_critique = any(m in blob for m in _FRAMED_BLOB_MARKERS)
    return bool(has_critique and has_decision)


def compose_estate_dispatch(
    *,
    db: Any = None,
    search: Any = None,
    limit: int = 10,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose Tirzah + Milcah + Mahalath + Deborah-infer dispatch maps.

    Later sources win on key collision only when explicitly passed in ``extra``;
    product defaults: tirzah base, then milcah, mahalath, infer, then extra.
    """
    dispatch: dict[str, Any] = {}
    try:
        from tirzah.deborah import deborah_dispatch as tirzah_dispatch

        dispatch.update(tirzah_dispatch(db=db, search=search, limit=limit))
    except Exception:
        pass
    try:
        from milcah.deborah import deborah_dispatch as milcah_dispatch  # type: ignore

        dispatch.update(milcah_dispatch())
    except Exception:
        pass
    try:
        from mahalath.deborah import deborah_dispatch as mahalath_dispatch  # type: ignore

        mdb = None
        try:
            from mahalath.config import load_config as mahalath_load_config
            from mahalath.db import get_database as mahalath_get_db

            mdb = mahalath_get_db(mahalath_load_config())
        except Exception:
            mdb = None
        dispatch.update(mahalath_dispatch(db=mdb))
    except Exception:
        pass
    try:
        from deborah.runtime.infer import deborah_infer_dispatch

        dispatch.update(deborah_infer_dispatch(use_llm=False))
    except Exception:
        pass
    if extra:
        dispatch.update(extra)
    return dispatch


def run_framed_plan(
    plan: Any,
    *,
    db: Any = None,
    tracer: Any = None,
    question: str | None = None,
    session_id: str | None = None,
    decisions: dict[str, str] | None = None,
    negotiate: bool = True,
    negotiator_name: str = "auto",
    require_conformance: bool = False,
    validate_profile: str = "full",
    open_questions_db: Any = None,
    search: Any = None,
) -> dict[str, Any]:
    """Run Deborah's substrate slice on a Tirzah (or Deborah) plan.

    Shares ``tracer`` and Mongo open-questions with the Tirzah session when
    provided. Fail-soft: returns structured error dict rather than raising for
    import/estate issues.
    """
    dplan = to_deborah_plan(plan)
    conf_errors = ensure_deborah_conformance(
        dplan, profile=validate_profile, require=require_conformance
    )

    try:
        from deborah.runtime.slice import run_substrate_slice
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "framed": True,
            "terminal": "blocked",
            "error": f"deborah.runtime unavailable: {type(exc).__name__}: {exc}",
            "deborah_conformance_errors": conf_errors,
            "slice": None,
            "open_question": None,
        }

    # Live estate: Tirzah Mongo + composed dispatch
    estate_ok = False
    estate_db = db
    try:
        from tirzah.deborah import prepare_live_estate

        estate = prepare_live_estate(db=db)
        estate_ok = bool(estate.get("ok"))
        estate_db = estate.get("db") if estate.get("db") is not None else db
        base_dispatch = dict(estate.get("dispatch") or {})
    except Exception:
        base_dispatch = {}

    dispatch = compose_estate_dispatch(
        db=estate_db, search=search, extra=base_dispatch or None
    )
    oq_db = open_questions_db if open_questions_db is not None else estate_db

    claim = question or dplan.get("request") or dplan.get("intent") or ""
    try:
        slice_result = run_substrate_slice(
            dplan,
            question=str(claim) if claim else None,
            demo=not estate_ok and not dispatch,
            live=estate_ok,
            dispatch=dispatch or None,
            open_questions_db=oq_db,
            use_live_open_questions=oq_db is not None,
            negotiate=negotiate,
            negotiator_name=negotiator_name,
            tracer=tracer,
            decisions=decisions,
            check_contracts=True,
            confidence_floor="low",
            post_retrieve_negotiate=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "framed": True,
            "terminal": "blocked",
            "error": f"run_substrate_slice failed: {type(exc).__name__}: {exc}",
            "deborah_conformance_errors": conf_errors,
            "slice": None,
            "open_question": None,
            "session_id": session_id,
        }

    return {
        "ok": slice_result.terminal in {"complete", "open"},
        "framed": True,
        "terminal": slice_result.terminal,
        "error": None,
        "deborah_conformance_errors": conf_errors,
        "slice": slice_result.to_dict(),
        "open_question": (
            slice_result.open_question.to_dict() if slice_result.open_question else None
        ),
        "session_id": session_id,
        "plan_id": dplan.get("plan_id"),
        "post_retrieve_negotiation": (
            slice_result.post_retrieve_negotiation.to_dict()
            if slice_result.post_retrieve_negotiation
            else None
        ),
    }


def framed_result_to_process_result(
    framed: dict[str, Any],
    *,
    query: str,
    session_id: str,
) -> dict[str, Any]:
    """Map ``run_framed_plan`` output into Tirzah process_frontend_request shape."""
    terminal = str(framed.get("terminal") or "blocked")
    slice_d = framed.get("slice") or {}
    run = slice_d.get("run") or {}
    oq = framed.get("open_question")
    answer_bits: list[str] = []
    if oq and isinstance(oq, dict):
        answer_bits.append(
            f"Open question: {oq.get('question') or ''} "
            f"(reason: {oq.get('reason') or 'residual'})"
        )
    # Prefer decide step selected
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        cog = str(step.get("cognition") or "").lower()
        res = step.get("result") if isinstance(step.get("result"), dict) else {}
        if cog == "decide" and res.get("selected") is not None:
            answer_bits.insert(0, f"Framed decision: {res.get('selected')}")
            break
    if not answer_bits:
        answer_bits.append(f"Framed Deborah run finished with terminal={terminal}.")
    if framed.get("error"):
        answer_bits.append(f"Error: {framed['error']}")

    process_trace = [
        {
            "step": "deborah.framed_execution",
            "input": {
                "plan_id": framed.get("plan_id"),
                "query": query,
                "session_id": session_id,
            },
            "output": {
                "terminal": terminal,
                "ok": framed.get("ok"),
                "conformance_errors": framed.get("deborah_conformance_errors") or [],
                "post_retrieve": framed.get("post_retrieve_negotiation"),
                "open_question_id": (oq or {}).get("open_question_id") if isinstance(oq, dict) else None,
            },
        }
    ]
    return {
        "ok": bool(framed.get("ok")),
        "query": query,
        "session_id": session_id,
        "answer": "\n".join(answer_bits),
        "reason": terminal,
        "framed_execution": True,
        "deborah_slice": slice_d,
        "open_question": oq,
        "deborah_conformance_errors": framed.get("deborah_conformance_errors") or [],
        "process_trace": process_trace,
    }
