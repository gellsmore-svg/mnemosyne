"""Outcomes-validation loop — the pure engine (family #5, phase 1).

Agentic work drifts from what it was started for. This module lets a process
declare **structured outcomes** and validates the accumulated work against them,
producing a per-outcome status and a drift score. It is deliberately pure and
side-effect-free: templates freeze outcomes into an instance (see
``templates``/``instances``), and this module scores work against them. The live
wiring — re-anchoring the planner and gating premature completion — is phase 2.

Validation is two-tier:

* a **deterministic floor** — cheap keyword-coverage of each outcome (and its
  optional ``check``) against the work produced so far. Always runs, offline.
* an optional **judgement tier** — a model (Milcah coherence pressure or a plain
  LLM ``ask``) rates whether each outcome is actually satisfied.

The deterministic status is always kept alongside any model status, so the
enforcement layer can honour the family rule: never auto-fail on a model alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

AskFn = Callable[[str], str]

CADENCES = ("every_revision", "on_complete", "every_n_calls")
ON_DRIFT_ACTIONS = ("log", "reanchor", "gate", "reanchor_then_gate")
OUTCOME_STATUSES = ("met", "partial", "unmet")

DEFAULT_CADENCE = "every_revision"
DEFAULT_ON_DRIFT = "reanchor_then_gate"
DEFAULT_DRIFT_THRESHOLD = 0.34

# Galeed / instance-trace event vocabulary (emitted in phase 2).
OUTCOMES_VALIDATED = "process.outcomes.validated"
OUTCOMES_DRIFT = "process.outcomes.drift"
OUTCOMES_REANCHORED = "process.outcomes.reanchored"
OUTCOMES_MET = "process.outcomes.met"

_MET_COVERAGE = 0.75
_PARTIAL_COVERAGE = 0.34

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "should", "shall",
    "must", "will", "when", "then", "than", "have", "has", "not", "are", "was",
    "were", "which", "each", "any", "all", "its", "his", "her", "their", "them",
    "a", "an", "of", "to", "in", "on", "as", "is", "it", "be", "by", "or", "at",
}


# --- normalisation (used by templates/instances) ---------------------------


def normalize_outcomes(raw: Any) -> list[dict[str, Any]]:
    """Normalise authored outcomes into ``[{id, statement, check}]``.

    Accepts a list of strings or dicts; auto-assigns ``O1``, ``O2``, … ids where
    missing. Raises ``ValueError`` on an empty statement.
    """
    if raw in (None, "", [], ()):
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("outcomes must be a list")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            statement, check, oid = item, "", ""
        elif isinstance(item, dict):
            statement = str(item.get("statement") or item.get("text") or "").strip()
            check = str(item.get("check") or "").strip()
            oid = str(item.get("id") or "").strip()
        else:
            raise ValueError("each outcome must be a string or an object")
        if not statement:
            raise ValueError("each outcome needs a non-empty statement")
        entry = {"id": oid or f"O{index}", "statement": statement}
        if check:
            entry["check"] = check
        out.append(entry)
    return out


def normalize_outcomes_loop(raw: Any) -> dict[str, Any] | None:
    """Normalise loop config, filling defaults.

    ``None`` ⇒ no loop (outcomes may be declared but unenforced). An object —
    even ``{}`` — arms the loop with defaults for any unspecified field.
    """
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise ValueError("outcomes_loop must be an object")
    cadence = str(raw.get("cadence") or DEFAULT_CADENCE)
    if cadence not in CADENCES:
        raise ValueError(f"invalid cadence {cadence!r}; allowed: {list(CADENCES)}")
    on_drift = str(raw.get("on_drift") or DEFAULT_ON_DRIFT)
    if on_drift not in ON_DRIFT_ACTIONS:
        raise ValueError(f"invalid on_drift {on_drift!r}; allowed: {list(ON_DRIFT_ACTIONS)}")
    threshold = raw.get("drift_threshold", DEFAULT_DRIFT_THRESHOLD)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("drift_threshold must be a number in [0, 1]") from exc
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("drift_threshold must be in [0, 1]")
    n = raw.get("n", 2)
    try:
        n = max(1, int(n))
    except (TypeError, ValueError) as exc:
        raise ValueError("n must be a positive integer") from exc
    return {"cadence": cadence, "n": n, "drift_threshold": threshold, "on_drift": on_drift}


# --- validation ------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _work_text(work: Any) -> str:
    if isinstance(work, str):
        return work
    if not isinstance(work, dict):
        return str(work)
    parts: list[str] = []
    for key in ("answer", "summary", "rendering", "reasoning", "objective"):
        value = work.get(key)
        if isinstance(value, str):
            parts.append(value)
    artifacts = work.get("artifacts")
    if isinstance(artifacts, dict):
        parts.append(json.dumps(artifacts, default=str))
    for step in work.get("plan_steps") or []:
        parts.append(step.get("action", "") if isinstance(step, dict) else str(step))
    for event in work.get("trace") or []:
        if isinstance(event, dict):
            parts.append(json.dumps(event.get("output", ""), default=str))
    return "\n".join(p for p in parts if p)


def _coverage(outcome: dict[str, Any], blob_tokens: set[str]) -> float:
    target = _tokens(outcome.get("check") or outcome["statement"])
    if not target:
        return 1.0
    return len(target & blob_tokens) / len(target)


def _deterministic_status(coverage: float) -> str:
    if coverage >= _MET_COVERAGE:
        return "met"
    if coverage >= _PARTIAL_COVERAGE:
        return "partial"
    return "unmet"


def _model_statuses(
    outcomes: list[dict[str, Any]], work_text: str, ask: AskFn
) -> dict[str, str]:
    prompt = (
        "You are auditing whether a piece of work satisfies its declared "
        "outcomes. For each outcome, decide met, partial, or unmet based ONLY on "
        "the work shown. Return ONLY a JSON array of "
        '{"id": "...", "status": "met|partial|unmet"} — no prose.\n\n'
        "OUTCOMES:\n"
        + "\n".join(
            f"- {o['id']}: {o['statement']}"
            + (f" (check: {o['check']})" if o.get("check") else "")
            for o in outcomes
        )
        + "\n\nWORK SO FAR:\n"
        + work_text[:6000]
    )
    try:
        raw = ask(prompt)
        data = json.loads(raw[raw.index("[") : raw.rindex("]") + 1])
        result: dict[str, str] = {}
        for item in data:
            if isinstance(item, dict):
                status = str(item.get("status", "")).lower()
                oid = str(item.get("id", ""))
                if status in OUTCOME_STATUSES and oid:
                    result[oid] = status
        return result
    except Exception:  # noqa: BLE001 - judgement tier is best-effort
        return {}


def validate_outcomes(
    instance: dict[str, Any], work: Any, *, ask: AskFn | None = None
) -> dict[str, Any]:
    """Score the work against an instance's frozen outcomes.

    Returns ``{ready, per_outcome, drift_score, drifting, threshold}``. When the
    instance declares no outcomes, ``ready`` is False and drift is zero.
    """
    outcomes = instance.get("process_outcomes") or []
    loop = instance.get("outcomes_loop") or {}
    threshold = float(loop.get("drift_threshold", DEFAULT_DRIFT_THRESHOLD))
    if not outcomes:
        return {
            "ready": False,
            "reason": "no outcomes declared",
            "per_outcome": [],
            "drift_score": 0.0,
            "drifting": False,
            "threshold": threshold,
        }

    work_text = _work_text(work)
    blob_tokens = _tokens(work_text)
    model_statuses = (
        _model_statuses(outcomes, work_text, ask) if ask is not None else {}
    )

    per_outcome: list[dict[str, Any]] = []
    unmet = 0
    for outcome in outcomes:
        coverage = _coverage(outcome, blob_tokens)
        det_status = _deterministic_status(coverage)
        model_status = model_statuses.get(outcome["id"])
        status = model_status or det_status
        if status == "unmet":
            unmet += 1
        per_outcome.append(
            {
                "id": outcome["id"],
                "statement": outcome["statement"],
                "status": status,
                "deterministic_status": det_status,
                "model_status": model_status,
                "coverage": round(coverage, 3),
            }
        )

    drift_score = round(unmet / len(outcomes), 3)
    return {
        "ready": True,
        "per_outcome": per_outcome,
        "drift_score": drift_score,
        "drifting": drift_score >= threshold,
        "threshold": threshold,
        "model_used": bool(model_statuses),
    }


def render_reanchor_constraint(validation: dict[str, Any]) -> str:
    """A re-anchoring instruction for the planner, naming the drifted outcomes.

    Used by phase 2 to inject into the next planning context; pure here so it can
    be unit-tested.
    """
    unmet = [o for o in validation.get("per_outcome", []) if o["status"] != "met"]
    if not unmet:
        return ""
    lines = [
        "OUTCOME RE-ANCHORING — the work is drifting from its declared outcomes. "
        "Re-align the next steps to satisfy, specifically:",
    ]
    for outcome in unmet:
        lines.append(f"- {outcome['id']} ({outcome['status']}): {outcome['statement']}")
    return "\n".join(lines)


def _new_check_id() -> str:
    return f"outcome_check_{uuid4().hex[:8]}"
