"""ITERATE and DECISION expansion for interpretive PLAN execution (SPEC §4.6)."""
from __future__ import annotations

import re
from typing import Any, Callable

from tirzah.planning.context_bundle import ensure_bundle, latest_tool_matches
from tirzah.planning.recursive import PlanStep

StepRunner = Callable[[PlanStep], dict[str, Any]]


def direct_body_steps(parent_id: str, steps: list[PlanStep]) -> list[PlanStep]:
    return [step for step in steps if parent_id in step.depends_on]


def is_owned_by_pending_parent(step: PlanStep, steps: list[PlanStep], completed: set[str]) -> bool:
    for parent in steps:
        if parent.id not in step.depends_on:
            continue
        if parent.construct in {"ITERATE", "DECISION"} and parent.id not in completed:
            return True
    return False


def parse_max_rounds(step: PlanStep, *, default: int = 3, ceiling: int = 10) -> int:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"MAX:\s*(\d+)", text, re.I)
    if match:
        return max(1, min(int(match.group(1)), ceiling))
    return max(1, min(default, ceiling))


def branch_label(step: PlanStep) -> str | None:
    for item in step.success_criteria:
        lowered = item.lower()
        if lowered.startswith("branch:"):
            return item.split(":", 1)[1].strip().lower()
    return None


def parse_decision_signal(step: PlanStep) -> str:
    match = re.search(r"ON:\s*([^→|]+)", step.action, re.I)
    if match:
        return _normalize_signal(match.group(1))
    for item in step.success_criteria:
        if item.lower().startswith("on:"):
            return _normalize_signal(item.split(":", 1)[1])
    return "default"


def _normalize_signal(value: str) -> str:
    return " ".join(str(value or "").split()).lower().replace(" ", "_")


def cascade_skip_dependents(
    steps: list[PlanStep],
    completed: set[str],
    trace: list[dict[str, Any]],
    *,
    reason: str = "skipped_parent",
) -> None:
    """Skip pending steps that depend on a skipped ancestor (nested branch trees)."""
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.status != "pending":
                continue
            parents = [next((row for row in steps if row.id == dep_id), None) for dep_id in step.depends_on]
            if not parents or not all(parent is not None and parent.status == "skipped" for parent in parents):
                continue
            step.status = "skipped"
            completed.add(step.id)
            trace.append(
                {
                    "step": "plan.step.skipped",
                    "step_id": step.id,
                    "metadata": {
                        "construct": step.construct,
                        "reason": reason,
                        "skipped_parents": [parent.id for parent in parents if parent is not None],
                    },
                }
            )
            changed = True


def parse_loop_control(step: PlanStep) -> tuple[str | None, str | None]:
    construct = (step.construct or "").upper()
    if construct not in {"BREAK", "CONTINUE"}:
        return None, None
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"IF:\s*([^;\]]+)", text, re.I)
    condition = match.group(1).strip().lower() if match else None
    return construct.lower(), condition


def evaluate_loop_control_condition(
    condition: str | None,
    *,
    artifacts: dict[str, Any],
    round_num: int,
    body_blocked: bool,
) -> bool:
    if condition is None:
        return True
    normalized = " ".join(condition.split()).lower().replace(" ", "_")
    if normalized in {"blocked", "body_blocked"}:
        return body_blocked
    if normalized == "has_matches":
        bundle = ensure_bundle(artifacts)
        return bool(latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")))
    if normalized == "context_ready":
        bundle = ensure_bundle(artifacts)
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        return "compile_context" in tools or bool(artifacts.get("retrieval_package"))
    if normalized.startswith("round>="):
        try:
            return round_num >= int(normalized.split(">=", 1)[1])
        except ValueError:
            return False
    return False


def iterate_until_satisfied(step: PlanStep, artifacts: dict[str, Any], *, round_num: int) -> bool:
    criteria = [
        item.split(":", 1)[1].strip().lower()
        for item in step.success_criteria
        if item.lower().startswith("until:")
    ]
    bundle = ensure_bundle(artifacts)
    if "has_matches" in criteria and latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")):
        return True
    if "context_ready" in criteria:
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        if "compile_context" in tools or artifacts.get("retrieval_package"):
            return True
    if "single_round" in criteria:
        return round_num >= 1
    return False


def evaluate_decision_branch(
    signal: str,
    *,
    artifacts: dict[str, Any],
    answer_kwargs: dict[str, Any],
    config: Any = None,
) -> str:
    runtime = getattr(config, "runtime", None) if config is not None else None
    if signal in {"web_research", "web_research_enabled", "external_evidence"}:
        if answer_kwargs.get("web_research") or getattr(runtime, "web_research_enabled", False):
            return "web"
        return "memory"
    if signal == "retrieval_mode":
        mode = answer_kwargs.get("retrieval_mode") or getattr(runtime, "retrieval_mode", "direct")
        return str(mode).lower()
    if signal in {"has_matches", "focus_selection"}:
        bundle = ensure_bundle(artifacts)
        if latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")):
            return "expand"
        return "search"
    if signal == "context_depth":
        bundle = ensure_bundle(artifacts)
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        if "compile_context" not in tools:
            return "compile"
        return "expand"
    return "default"


def execute_iterate_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    body = direct_body_steps(step.id, steps)
    if not body:
        return {"status": "completed", "artifact": {"rounds": 0, "body": []}}
    max_rounds = parse_max_rounds(step)
    rounds_run = 0
    loop_break = False
    for round_num in range(1, max_rounds + 1):
        rounds_run = round_num
        trace.append(
            {
                "step": "plan.iterate.round",
                "step_id": step.id,
                "metadata": {"round": round_num, "max_rounds": max_rounds, "body": [item.id for item in body]},
            }
        )
        body_blocked = False
        for body_step in body:
            if round_num > 1 and body_step.status in {"completed", "skipped"}:
                if (body_step.construct or "").upper() not in {"BREAK", "CONTINUE"}:
                    body_step.status = "pending"
                    completed.discard(body_step.id)
            if body_step.status != "pending":
                continue
            if not all(dep in completed or dep == step.id for dep in body_step.depends_on):
                continue
            control, condition = parse_loop_control(body_step)
            if control == "break":
                if evaluate_loop_control_condition(
                    condition,
                    artifacts=artifacts,
                    round_num=round_num,
                    body_blocked=body_blocked,
                ):
                    body_step.status = "completed"
                    completed.add(body_step.id)
                    trace.append(
                        {
                            "step": "plan.step.completed",
                            "step_id": body_step.id,
                            "metadata": {
                                "construct": "BREAK",
                                "reason": "loop_break",
                                "round": round_num,
                                "condition": condition,
                            },
                        }
                    )
                    loop_break = True
                    break
                body_step.status = "skipped"
                completed.add(body_step.id)
                trace.append(
                    {
                        "step": "plan.step.skipped",
                        "step_id": body_step.id,
                        "metadata": {"construct": "BREAK", "reason": "condition_not_met", "round": round_num},
                    }
                )
                continue
            if control == "continue":
                if evaluate_loop_control_condition(
                    condition,
                    artifacts=artifacts,
                    round_num=round_num,
                    body_blocked=body_blocked,
                ):
                    body_step.status = "completed"
                    completed.add(body_step.id)
                    trace.append(
                        {
                            "step": "plan.step.completed",
                            "step_id": body_step.id,
                            "metadata": {
                                "construct": "CONTINUE",
                                "reason": "loop_continue",
                                "round": round_num,
                                "condition": condition,
                            },
                        }
                    )
                    break
                body_step.status = "skipped"
                completed.add(body_step.id)
                trace.append(
                    {
                        "step": "plan.step.skipped",
                        "step_id": body_step.id,
                        "metadata": {"construct": "CONTINUE", "reason": "condition_not_met", "round": round_num},
                    }
                )
                continue
            outcome = run_step(body_step)
            body_step.status = outcome["status"]
            trace.append(
                {
                    "step": f"plan.step.{outcome['status']}",
                    "step_id": body_step.id,
                    "metadata": {"construct": body_step.construct, "reason": outcome.get("reason"), "round": round_num},
                }
            )
            if outcome["status"] == "completed":
                completed.add(body_step.id)
                if outcome.get("artifact") is not None:
                    artifacts[body_step.id] = outcome["artifact"]
            if outcome["status"] == "blocked":
                body_blocked = True
        if loop_break:
            break
        if iterate_until_satisfied(step, artifacts, round_num=round_num):
            break
        if any(body_step.status == "blocked" for body_step in body):
            break
    return {
        "status": "completed",
        "artifact": {
            "rounds": rounds_run,
            "body": [item.id for item in body],
            "max_rounds": max_rounds,
            "loop_break": loop_break,
        },
    }


def execute_decision_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    answer_kwargs: dict[str, Any],
    config: Any,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    signal = parse_decision_signal(step)
    chosen = evaluate_decision_branch(signal, artifacts=artifacts, answer_kwargs=answer_kwargs, config=config)
    artifacts[f"decision:{step.id}"] = chosen
    branches = direct_body_steps(step.id, steps)
    selected = []
    fallback_id = None
    for branch in branches:
        label = branch_label(branch) or "default"
        if label == chosen:
            selected.append(branch.id)
        elif label == "default":
            fallback_id = branch.id
    if not selected and fallback_id:
        selected = [fallback_id]
    elif not selected and branches:
        selected = [branches[0].id]
    for branch in branches:
        if branch.id in selected:
            continue
        branch.status = "skipped"
        completed.add(branch.id)
        trace.append(
            {
                "step": "plan.step.skipped",
                "step_id": branch.id,
                "metadata": {"construct": branch.construct, "reason": "decision_not_selected", "branch": chosen},
            }
        )
    cascade_skip_dependents(steps, completed, trace, reason="decision_branch_skipped")
    trace.append(
        {
            "step": "plan.decision.selected",
            "step_id": step.id,
            "metadata": {"signal": signal, "branch": chosen, "selected_steps": selected},
        }
    )
    return {"status": "completed", "artifact": {"signal": signal, "branch": chosen, "selected_steps": selected}}


def suggest_plan_profile_hint(query: str, answer_kwargs: dict[str, Any], config: Any) -> str:
    runtime = getattr(config, "runtime", None)
    web = bool(answer_kwargs.get("web_research") or getattr(runtime, "web_research_enabled", False))
    mode = str(answer_kwargs.get("retrieval_mode") or getattr(runtime, "retrieval_mode", "direct")).lower()
    word_count = len(str(query or "").split())
    if web:
        return "Use granular context profile: search_nodes → compile_context → web_search → web_fetch → answer_adapter."
    if mode in {"agentic", "deep"}:
        return "Prefer granular context with graph expansion (search_nodes, compile_context, expand_proximity) unless a monolithic backbone is clearly sufficient."
    if word_count >= 14:
        return "Long request: consider granular gather steps with explicit ITERATE bounds instead of one monolithic retrieval."
    return "Prefer monolithic tirzah_retrieval → answer_adapter unless the request needs scoped DB/web steps or revision-friendly gather."