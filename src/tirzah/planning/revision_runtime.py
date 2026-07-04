"""Mid-step PLAN revision during interpretive execution."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from tirzah.config import AppConfig
from tirzah.planning.recursive import CairnPlan, PlanStep, PlannerFn, information_from_result, revise_plan


def apply_mid_step_revision(
    plan: CairnPlan,
    working_steps: list[PlanStep],
    completed: set[str],
    context: Any,
    *,
    planner: PlannerFn,
    config: AppConfig,
    last_step_id: str,
    last_outcome: dict[str, Any],
) -> tuple[list[PlanStep], CairnPlan, bool]:
    """Finish the current step, then swap in a revised plan when the planner says revise."""
    info: dict[str, Any] = {
        "mid_step_revision": True,
        "completed_step_id": last_step_id,
        "step_status": last_outcome.get("status"),
        "step_reason": last_outcome.get("reason"),
        "artifact_keys": sorted(context.artifacts.keys()),
    }
    primary = context.artifacts.get("synthesis_result") or context.artifacts.get("retrieval_result")
    if isinstance(primary, dict):
        info.update(information_from_result(primary))
    revised = revise_plan(
        replace(plan, steps=working_steps),
        info,
        planner=planner,
        max_steps=config.runtime.planning_max_steps,
    )
    if revised.revision <= plan.revision or revised.revision_decision != "revise":
        return working_steps, plan, False
    old_status = {step.id: step.status for step in working_steps}
    new_steps: list[PlanStep] = []
    for step in revised.steps:
        if step.id in completed:
            new_steps.append(replace(step, status="completed"))
        elif step.id in old_status:
            new_steps.append(replace(step, status=old_status[step.id]))
        else:
            new_steps.append(step)
    context.trace.append(
        {
            "step": "plan.revision.mid_step",
            "step_id": last_step_id,
            "metadata": {
                "revision": revised.revision,
                "parent_revision": revised.parent_revision,
                "revision_decision": revised.revision_decision,
            },
        }
    )
    return new_steps, revised, True