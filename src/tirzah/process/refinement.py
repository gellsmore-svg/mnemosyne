"""Tirzah-assisted process authoring (v2 refinement assistant).

Two aids for making a plain-text process better before it is used:

- ``review_process`` — Tirzah reads a draft process body and returns clarifying
  questions, findings (gaps, ambiguities, a missing-gate warning), and an
  optional improved body. Deterministic structural checks (does it name gates?
  is it too short? are steps numbered?) run regardless of the model, so the
  review is useful even when the LLM is unavailable.

- ``trial_process`` — a dry run: plan a *sample* task under the process (via the
  interpretive planner's constraint) and report the resulting step shape — did
  the plan place the gate steps the process demands? — WITHOUT executing any
  tools. This validates intended behaviour before full activation.

Both take an injectable ``ask`` (a ``str -> str`` LLM call) so they are testable
without a model; the default wires Tirzah's answer adapter.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tirzah.process.enforcement import process_is_override, process_requires_gate

AskFn = Callable[[str], str]

_FINDING_KINDS = ("gap", "ambiguity", "missing_gate", "strength")


def default_ask(config: Any) -> AskFn:
    """An ``ask`` backed by Tirzah's planner-runtime answer adapter."""
    from tirzah.adapters.answer import answer_adapter
    from tirzah.planning.recursive import planner_runtime_config

    runtime = planner_runtime_config(config.runtime)

    def ask(prompt: str) -> str:
        result = answer_adapter(runtime).answer(
            {"prompt_text": prompt, "context_text": "", "context_metadata": {"included": []}}
        )
        return str(result.get("answer") or "")

    return ask


def review_process(
    body: str,
    *,
    ask: AskFn | None = None,
    intended_use: str = "",
) -> dict[str, Any]:
    """Review a draft process body. Returns clarifying questions + findings +
    an optional suggested body. Never raises: the LLM half is best-effort and
    the structural half always runs."""
    body = body or ""
    findings = _structural_findings(body)
    questions: list[str] = []
    suggested_body: str | None = None
    model_used = False

    if ask is not None and body.strip():
        try:
            raw = ask(_review_prompt(body, intended_use))
            parsed = _extract_json(raw)
            if isinstance(parsed, dict):
                model_used = True
                for q in parsed.get("clarifying_questions") or []:
                    if isinstance(q, str) and q.strip():
                        questions.append(q.strip())
                for f in parsed.get("findings") or []:
                    if isinstance(f, dict):
                        kind = str(f.get("kind") or "gap")
                        note = str(f.get("note") or "").strip()
                        if note:
                            findings.append({
                                "kind": kind if kind in _FINDING_KINDS else "gap",
                                "note": note,
                                "source": "model",
                            })
                candidate = parsed.get("suggested_body")
                if isinstance(candidate, str) and candidate.strip():
                    suggested_body = candidate.strip()
        except Exception:
            model_used = False

    return {
        "ok": True,
        "has_gates": process_requires_gate(body),
        "is_override": process_is_override(body),
        "clarifying_questions": questions,
        "findings": findings,
        "suggested_body": suggested_body,
        "model_used": model_used,
    }


def trial_process(
    db: Any,
    config: Any,
    *,
    body: str,
    sample_task: str,
    planner: Any = None,
) -> dict[str, Any]:
    """Dry-run a process against a sample task: plan under it, report the plan's
    step shape and whether it placed gate steps — no tools executed.

    Returns ``{ok, plan_steps, has_gate_steps, gate_expected, plan_matches_gates}``.
    """
    from tirzah.planning.recursive import create_initial_plan, make_planner

    planner = planner or make_planner(config.runtime)
    constraint = _trial_constraint(body)
    try:
        plan = create_initial_plan(
            sample_task,
            planner=planner,
            max_steps=config.runtime.planning_max_steps,
            context=constraint,
        )
    except Exception as error:  # pragma: no cover - planner robustness
        return {"ok": False, "reason": f"planning_failed: {error}"}

    steps = [
        {
            "id": step.id,
            "construct": (step.construct or "").upper(),
            "action": step.action,
            "allowed_tools": list(step.allowed_tools or []),
        }
        for step in plan.steps
    ]
    has_gate_steps = any(s["construct"] == "AWAIT" for s in steps)
    gate_expected = process_requires_gate(body)
    return {
        "ok": True,
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "plan_steps": steps,
        "has_gate_steps": has_gate_steps,
        "gate_expected": gate_expected,
        # The process's gate intent is honoured iff a gate is present exactly
        # when the process asks for one.
        "plan_matches_gates": has_gate_steps == gate_expected,
    }


# --- internals -------------------------------------------------------------


def _structural_findings(body: str) -> list[dict[str, Any]]:
    """Model-free checks that always apply."""
    findings: list[dict[str, Any]] = []
    stripped = body.strip()
    if len(stripped) < 40:
        findings.append({
            "kind": "gap",
            "note": "The process is very short — state the steps and where human "
            "judgement is required.",
            "source": "structural",
        })
    if not process_requires_gate(body) and not process_is_override(body):
        findings.append({
            "kind": "missing_gate",
            "note": "No human gate is stated. If any step applies a change or "
            "ships work, say where to pause for approval (e.g. 'pause for "
            "approval before shipping'). Omit only for deliberately fluid work.",
            "source": "structural",
        })
    has_numbering = any(
        line.strip()[:2].rstrip(".").isdigit()
        for line in body.splitlines()
        if line.strip()
    )
    if not has_numbering and len(stripped) >= 40:
        findings.append({
            "kind": "ambiguity",
            "note": "The steps are not clearly enumerated — numbering them makes "
            "the order the agent must follow unambiguous.",
            "source": "structural",
        })
    return findings


def _review_prompt(body: str, intended_use: str) -> str:
    use = f"\nIntended use: {intended_use}\n" if intended_use.strip() else ""
    return (
        "You are Tirzah, helping an operator author a work PROCESS (plain-text "
        "prose describing how agentic work should proceed — its steps, its "
        "human-approval gates, and its review loops).\n"
        "Review the draft below. Identify where it is ambiguous or has gaps, ask "
        "the clarifying questions a human should answer, and — only if you can "
        "genuinely improve it while keeping the author's intent and plain-text "
        "style — propose a revised body.\n"
        "Return STRICT JSON only:\n"
        '{ "clarifying_questions": ["…"], '
        '"findings": [{"kind": "gap|ambiguity|missing_gate|strength", "note": "…"}], '
        '"suggested_body": "…or omit" }\n'
        f"{use}\n"
        "DRAFT PROCESS:\n"
        f"{body}\n"
    )


def _trial_constraint(body: str) -> str:
    """The process text as a planning constraint for a trial run (mirrors
    enforcement.render_process_constraint but without a live instance)."""
    from tirzah.process.enforcement import render_process_constraint

    return render_process_constraint({"process_body": body, "template_name": "draft process"})


def _extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        import re

        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    decoder = json.JSONDecoder(strict=False)
    import re

    for match in re.finditer(r"[{\[]", stripped):
        try:
            parsed, _ = decoder.raw_decode(stripped[match.start():])
            return parsed
        except json.JSONDecodeError:
            continue
    return None
