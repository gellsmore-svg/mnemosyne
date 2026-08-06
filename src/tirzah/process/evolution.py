"""Auto-evolve process templates from accumulated retrospective data.

Processes are living artifacts: real-world usage reveals where a process is
wrong. This module mines the instances of a template for recurring patterns —
deviations people keep taking (and getting approved), gates that keep getting
rejected, heavy override or abandonment rates — and turns them into a
**proposed** revised template body with a rationale.

The proposal is never auto-applied. ``apply_evolution`` creates a new version
only when a human approves it (recording provenance), and — because instances
freeze their process body at bind time — active work is unaffected: evolution
is backward-compatible by construction. This keeps the human-grounding
principle: usage informs, a person decides.

That gate covers **writes**. ``assess_reuse`` covers the other side: before a
template version is bound to new work, check whether it is still carrying its
weight. A version can be approved and then never actually validated by a
completed run, or can start exhibiting the very symptoms that triggered the last
evolution. Published evidence on learned-procedure reuse is blunt about this —
reusing accumulated procedure *without* a re-check degraded task success by
11–14 points across a real version migration, while a gated equivalent held
flat; the damage is silent, because the stale procedure still looks plausible.

``assess_reuse`` deliberately does **not** return a pass/fail. A concern is not
a veto: the immediate work comes first, and an assessment that blocked service
would be worse than the drift it guards against. It returns concerns with the
evidence behind them and an ordinal recommendation, which ``start_instance``
records on the instance trace.
"""

from __future__ import annotations

from typing import Any, Callable

from pymongo.database import Database

from tirzah.process import instances as inst
from tirzah.process.templates import get_template, revise_template

AskFn = Callable[[str], str]

# Thresholds for flagging a signal worth acting on.
_MIN_INSTANCES = 3          # need some history before proposing changes
_RECUR_DEVIATION = 2        # a deviation seen this many times is a pattern
_HIGH_OVERRIDE_RATE = 0.34  # gates may be heavier than the work warrants
_HIGH_ABANDON_RATE = 0.34   # process may be too onerous / unclear
_HIGH_GATE_REJECT = 2       # a step whose gate keeps getting rejected


def assess_reuse(
    db: Database, template_id: str, *, version: int | None = None
) -> dict[str, Any]:
    """Re-check a template version before it is bound to new work.

    Returns ``{ok, template_id, version, concerns, evidence, recommendation}``.

    ``recommendation`` is ordinal, never boolean:

    - ``proceed`` — nothing observed against this version;
    - ``proceed_with_note`` — a concern worth recording, not worth blocking;
    - ``prefer_previous_version`` — this version looks worse than the one it
      replaced, on its own instances.

    Concerns are evidence-bearing (``{kind, detail, evidence}``) so a later
    reviewer can see what the judgement was made from, and so a concern that
    turns out to be wrong can be argued with rather than merely overridden.

    Only signals computable from recorded instances are used. Notably absent:
    any check on the *model* that authored a version — nothing in the template
    record identifies it, and inventing that signal would be worse than
    omitting it.
    """
    template = get_template(db, template_id, version=version)
    if template is None:
        return {
            "ok": False,
            "reason": "unknown_template",
            "template_id": template_id,
            "version": version,
            "concerns": [],
            "recommendation": "proceed",  # never block on our own lookup failure
        }

    resolved = int(template["version"])
    rows = inst.list_instances(db, template_id=template_id, limit=500)
    on_version = [r for r in rows if int(r.get("template_version") or 0) == resolved]
    completed = [r for r in on_version if r.get("status") == "completed"]

    concerns: list[dict[str, Any]] = []

    # 1. Approved but never proven. The highest-value signal: a revision was
    #    accepted and has never once been carried through to completion.
    if resolved > 1 and not completed:
        concerns.append({
            "kind": "never_validated",
            "detail": (
                f"v{resolved} has {len(on_version)} instance(s) and none completed. "
                "The revision was approved but has not yet been shown to work."
            ),
            "evidence": {"version": resolved, "instances": len(on_version), "completed": 0},
        })

    # 2. Regression: this version already shows the symptoms that trigger
    #    evolution, i.e. the last revision did not fix what it aimed at.
    n = len(on_version)
    if n >= _MIN_INSTANCES:
        abandoned = sum(1 for r in on_version if r.get("status") == "abandoned")
        overrides = sum(
            1
            for r in on_version
            for e in (r.get("trace") or [])
            if e.get("event") == "process.override.invoked"
        )
        abandon_rate = round(abandoned / n, 3)
        override_rate = round(overrides / n, 3)
        if abandon_rate >= _HIGH_ABANDON_RATE:
            concerns.append({
                "kind": "abandonment_on_current_version",
                "detail": (
                    f"{abandon_rate:.0%} of v{resolved} instances were abandoned — "
                    "the same signal that prompts evolution is present on the "
                    "version meant to have fixed it."
                ),
                "evidence": {"abandon_rate": abandon_rate, "abandoned": abandoned, "n": n},
            })
        if override_rate >= _HIGH_OVERRIDE_RATE:
            concerns.append({
                "kind": "overrides_on_current_version",
                "detail": (
                    f"{override_rate:.0%} of v{resolved} instances used an emergency "
                    "override — its gates are still heavier than the work warrants."
                ),
                "evidence": {"override_rate": override_rate, "overrides": overrides, "n": n},
            })

    recommendation = "proceed"
    if concerns:
        regressed = {"abandonment_on_current_version", "overrides_on_current_version"}
        recommendation = (
            "prefer_previous_version"
            if resolved > 1 and any(c["kind"] in regressed for c in concerns)
            else "proceed_with_note"
        )

    return {
        "ok": True,
        "template_id": template_id,
        "version": resolved,
        "concerns": concerns,
        "evidence": {"instances_on_version": len(on_version), "completed": len(completed)},
        "recommendation": recommendation,
    }


def analyze_template_evolution(db: Database, template_id: str) -> dict[str, Any]:
    """Roll up a template's instances into evolution findings (evidence-backed).

    Returns ``{ok, template_id, instance_count, findings, ready}`` — ``ready``
    is False (with a reason) when there is too little history to act on.
    """
    template = get_template(db, template_id)
    if template is None:
        return {"ok": False, "reason": "unknown_template", "findings": []}
    instances = inst.list_instances(db, template_id=template_id, limit=500)
    n = len(instances)
    if n < _MIN_INSTANCES:
        return {
            "ok": True, "template_id": template_id, "instance_count": n,
            "ready": False,
            "reason": f"need at least {_MIN_INSTANCES} instances ({n} so far)",
            "findings": [],
        }

    approved_deviations: dict[str, int] = {}
    gate_rejections: dict[str, int] = {}
    overrides = 0
    abandoned = 0
    for instance in instances:
        trace = instance.get("trace") or []
        if instance.get("status") == "abandoned":
            abandoned += 1
        # A deviation description that was subsequently APPROVED is a candidate
        # to fold into the process.
        pending_deviation: str | None = None
        for event in trace:
            name = event.get("event")
            detail = event.get("detail") or {}
            if name == "process.deviation.flagged":
                pending_deviation = _norm(str(detail.get("description") or ""))
            elif name == "process.deviation.approved" and pending_deviation:
                approved_deviations[pending_deviation] = approved_deviations.get(pending_deviation, 0) + 1
                pending_deviation = None
            elif name == "process.gate.rejected":
                step = str(detail.get("step_id") or "?")
                gate_rejections[step] = gate_rejections.get(step, 0) + 1
            elif name == "process.override.invoked":
                overrides += 1

    findings: list[dict[str, Any]] = []

    for description, count in sorted(approved_deviations.items(), key=lambda kv: kv[1], reverse=True):
        if count >= _RECUR_DEVIATION:
            findings.append({
                "kind": "fold_deviation",
                "note": f"A deviation was flagged and approved {count} times: "
                f"\"{description}\". If it keeps being approved, it belongs in the "
                "process rather than being flagged each time.",
                "evidence": {"description": description, "approved_count": count},
                "suggested_change": f"Allow (or fold in) the practice: {description}",
            })

    for step, count in sorted(gate_rejections.items(), key=lambda kv: kv[1], reverse=True):
        if count >= _HIGH_GATE_REJECT:
            findings.append({
                "kind": "gate_friction",
                "note": f"The gate at step {step} was rejected {count} times — its "
                "criteria may be unclear or the step may need splitting so the "
                "review is decidable.",
                "evidence": {"step_id": step, "rejections": count},
                "suggested_change": f"Clarify the success criteria for step {step}.",
            })

    override_rate = round(overrides / n, 3)
    if override_rate >= _HIGH_OVERRIDE_RATE:
        findings.append({
            "kind": "gates_too_heavy",
            "note": f"{override_rate:.0%} of instances used an emergency override — "
            "the gates may be heavier than this work actually warrants.",
            "evidence": {"override_rate": override_rate, "overrides": overrides},
            "suggested_change": "Consider lighter gates, or a faster variant for "
            "the common case.",
        })

    abandon_rate = round(abandoned / n, 3)
    if abandon_rate >= _HIGH_ABANDON_RATE:
        findings.append({
            "kind": "high_abandonment",
            "note": f"{abandon_rate:.0%} of instances were abandoned — the process "
            "may be too onerous or unclear to complete.",
            "evidence": {"abandon_rate": abandon_rate, "abandoned": abandoned},
            "suggested_change": "Simplify the process or clarify its steps.",
        })

    return {
        "ok": True,
        "template_id": template_id,
        "template_name": template.get("name"),
        "base_version": template.get("version"),
        "instance_count": n,
        "ready": bool(findings),
        "reason": None if findings else "no evolution signals above threshold",
        "findings": findings,
    }


def propose_evolution(
    db: Database, template_id: str, *, ask: AskFn | None = None
) -> dict[str, Any]:
    """Produce a proposed revised body from the analysis (does NOT apply it).

    Deterministic by default (appends an evidence-based "evolution notes"
    section to the current body); with ``ask``, the model synthesises a cleaner
    integrated rewrite. Returns the analysis + ``proposed_body`` + ``rationale``.
    """
    analysis = analyze_template_evolution(db, template_id)
    if not analysis.get("ok"):
        return analysis
    if not analysis.get("ready"):
        return {**analysis, "proposed_body": None, "rationale": None, "model_used": False}

    template = get_template(db, template_id)
    current_body = template["body"]
    findings = analysis["findings"]
    rationale = "; ".join(f["kind"] for f in findings)

    proposed_body = None
    model_used = False
    if ask is not None:
        try:
            raw = ask(_rewrite_prompt(current_body, findings))
            candidate = raw.strip()
            # Guard against a model that returns nothing useful.
            if len(candidate) >= max(40, int(len(current_body) * 0.5)):
                proposed_body = candidate
                model_used = True
        except Exception:
            model_used = False

    if proposed_body is None:
        proposed_body = _deterministic_body(current_body, findings, analysis["instance_count"])

    return {
        **analysis,
        "proposed_body": proposed_body,
        "rationale": rationale,
        "model_used": model_used,
    }


def apply_evolution(
    db: Database,
    template_id: str,
    *,
    body: str,
    rationale: str,
    based_on_instances: int,
    created_by: str = "operator",
) -> dict[str, Any]:
    """Land an approved evolution as a new template version, with provenance.

    Human-gated: call this only after a person approves the proposed body.
    Active instances are unaffected (they froze their body at bind time)."""
    if not body.strip():
        raise ValueError("evolved body is required")
    return revise_template(
        db,
        template_id,
        body=body,
        created_by=created_by,
        provenance={
            "kind": "evolution",
            "rationale": rationale,
            "based_on_instances": based_on_instances,
        },
    )


# --- internals -------------------------------------------------------------


def _deterministic_body(body: str, findings: list[dict[str, Any]], n: int) -> str:
    lines = [
        body.rstrip(),
        "",
        f"## Evolution notes (proposed from {n} past run(s) — review before adopting)",
    ]
    for f in findings:
        lines.append(f"- {f['suggested_change']}  ({f['kind']})")
    return "\n".join(lines) + "\n"


def _rewrite_prompt(body: str, findings: list[dict[str, Any]]) -> str:
    findings_text = "\n".join(f"- ({f['kind']}) {f['note']}" for f in findings)
    return (
        "You maintain a work PROCESS (plain-text prose). Real-world usage has "
        "surfaced the patterns below. Produce an improved version of the process "
        "that folds in what should be folded in and clarifies what is unclear, "
        "keeping the author's plain-text style and its overall intent. Preserve "
        "any human-approval gates unless the evidence says they are too heavy. "
        "Return ONLY the revised process text (no preamble, no JSON).\n\n"
        f"CURRENT PROCESS:\n{body}\n\n"
        f"OBSERVED PATTERNS:\n{findings_text}\n"
    )


def _norm(text: str) -> str:
    """Normalise a deviation description for grouping (lowercase, collapse ws)."""
    return " ".join((text or "").lower().split())
