"""Smart process auto-selection — suggest a process from task characteristics.

Given a task description (and optional explicit risk/scope), suggest which
template to run it under, with a reason. Two tiers, both overridable by the
operator:

- **Deterministic** (always): score every template by risk/scope alignment and
  urgency/keyword signals read from the task text. Zero-dependency, explainable,
  and the sole basis when no model is available.
- **LLM re-rank** (optional): if an ``ask`` is supplied, the model picks among
  the top deterministic candidates for a nuanced tie-break; its pick is only
  honoured when it names a real candidate, so it can refine but not fabricate.

The suggestion is advisory: ``start_instance`` records ``selection_reason`` so
the audit trail shows whether the operator took the suggestion or overrode it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tirzah.process.templates import list_templates

AskFn = Callable[[str], str]

# Signals in task text that push toward heavier or lighter governance.
_URGENT_WORDS = {
    "urgent", "emergency", "critical", "outage", "down", "hotfix", "incident",
    "breach", "asap", "immediately",
}
_URGENT_PHRASES = ("prod is down", "production is down", "is down", "right now")
_HIGH_RISK_WORDS = {
    "production", "prod", "payment", "billing", "auth", "security", "migration",
    "migrate", "database", "delete", "irreversible", "release", "ship",
    "deploy", "launch",
}
_HIGH_RISK_PHRASES = ("customer data",)
_LOW_RISK_WORDS = {
    "experiment", "spike", "prototype", "draft", "explore", "investigate",
    "research", "docs", "documentation", "typo", "cleanup", "refactor", "test",
}

# Risk band → preferred preset (used as a fallback ordering hint).
_RISK_PREFERENCE = {"high": "high", "medium": "medium", "low": "low"}


def suggest_process(
    db: Any,
    *,
    task: str,
    risk_level: str | None = None,
    scope: str | None = None,
    ask: AskFn | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Suggest a template for ``task``. Returns the chosen template id + reason +
    the ranked candidates (so a UI can show alternatives)."""
    templates = list_templates(db)
    if not templates:
        return {"ok": False, "reason": "no_templates", "suggested_template_id": None, "candidates": []}

    inferred_risk = risk_level or _infer_risk(task)
    signals = _task_signals(task)
    ranked = _rank(templates, inferred_risk, scope, signals)
    candidates = ranked[: max(1, min(int(top_k), len(ranked)))]

    chosen = candidates[0]
    reason = chosen["reason"]
    method = "deterministic"

    if ask is not None and len(candidates) > 1:
        picked = _llm_pick(ask, task, inferred_risk, candidates)
        if picked is not None:
            chosen = picked
            reason = f"model-selected among top candidates ({picked['reason']})"
            method = "model"

    return {
        "ok": True,
        "suggested_template_id": chosen["template_id"],
        "suggested_template_name": chosen["name"],
        "reason": reason,
        "method": method,
        "inferred_risk": inferred_risk,
        "signals": sorted(signals),
        "candidates": [
            {
                "template_id": c["template_id"],
                "name": c["name"],
                "score": c["score"],
                "reason": c["reason"],
            }
            for c in candidates
        ],
    }


# --- deterministic scoring -------------------------------------------------


def _task_signals(task: str) -> set[str]:
    words = _words(task)
    lowered = (task or "").lower()
    signals: set[str] = set()
    if words & _URGENT_WORDS or any(p in lowered for p in _URGENT_PHRASES):
        signals.add("urgent")
    if words & _HIGH_RISK_WORDS or any(p in lowered for p in _HIGH_RISK_PHRASES):
        signals.add("high_risk")
    if words & _LOW_RISK_WORDS:
        signals.add("low_risk")
    return signals


def _infer_risk(task: str) -> str:
    signals = _task_signals(task)
    if "urgent" in signals:
        return "high"
    if "high_risk" in signals:
        return "high"
    if "low_risk" in signals:
        return "low"
    return "medium"


def _rank(
    templates: list[dict[str, Any]],
    inferred_risk: str,
    scope: str | None,
    signals: set[str],
) -> list[dict[str, Any]]:
    scored = []
    for template in templates:
        score = 0.0
        reasons: list[str] = []

        t_risk = template.get("risk_level")
        if t_risk and t_risk == inferred_risk:
            score += 3.0
            reasons.append(f"risk match ({t_risk})")
        elif t_risk and _RISK_PREFERENCE.get(t_risk) == inferred_risk:
            score += 2.0

        if scope and template.get("scope") == scope:
            score += 1.5
            reasons.append(f"scope match ({scope})")

        name = (template.get("name") or "").lower()
        # Emergency-shaped process for urgent tasks.
        if "urgent" in signals and ("emergency" in name or template.get("category") == "governance" and t_risk == "high"):
            if "emergency" in name:
                score += 4.0
                reasons.append("urgent task → emergency process")
        # Fluid-shaped process for clearly low-risk work.
        if "low_risk" in signals and inferred_risk == "low":
            if "fluid" in name or t_risk == "low":
                score += 2.5
                reasons.append("low-risk task → lighter process")
        # Governed for high-risk non-urgent.
        if "high_risk" in signals and "urgent" not in signals and t_risk == "high" and "emergency" not in name:
            score += 2.5
            reasons.append("high-risk task → governed process")

        scored.append({
            **template,
            "score": round(score, 2),
            "reason": "; ".join(reasons) or "default ranking",
        })
    scored.sort(key=lambda t: (t["score"], t.get("is_preset", False)), reverse=True)
    return scored


def _llm_pick(
    ask: AskFn, task: str, inferred_risk: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    prompt = (
        "Pick the single most appropriate work PROCESS for the task. Choose ONLY "
        "from the candidates by their template_id. Consider risk and how much "
        "human oversight the task warrants.\n"
        f"Task: {task}\n"
        f"Inferred risk: {inferred_risk}\n"
        "Candidates:\n"
        + json.dumps(
            [
                {"template_id": c["template_id"], "name": c["name"],
                 "risk_level": c.get("risk_level"), "description": c.get("description", "")}
                for c in candidates
            ],
            indent=2,
        )
        + '\n\nReturn STRICT JSON: {"template_id": "…"}'
    )
    try:
        parsed = _extract_json(ask(prompt))
        if isinstance(parsed, dict):
            picked_id = parsed.get("template_id")
            return next((c for c in candidates if c["template_id"] == picked_id), None)
    except Exception:
        return None
    return None


def _words(text: str) -> set[str]:
    return {
        token
        for token in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split()
        if len(token) > 1
    }


def _extract_json(text: str) -> Any:
    import re

    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"[{\[]", stripped):
        try:
            parsed, _ = decoder.raw_decode(stripped[match.start():])
            return parsed
        except json.JSONDecodeError:
            continue
    return None
