"""Interpretive Cairn PLAN execution (SPEC §4.6).

Walks a machine plan in dependency order, dispatches CALL steps through a handler
registry constrained by allowed_tools, and records per-step status + trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from tirzah.planning.recursive import ALLOWED_PLAN_TOOLS, CairnPlan, PlanStep

StepHandler = Callable[[PlanStep, "PlanExecutionContext"], dict[str, Any]]


@dataclass
class PlanExecutionContext:
    query: str
    session_id: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    effects: set[str] = field(default_factory=set)  # once-only handlers (e.g. tirzah_retrieval)


@dataclass
class PlanExecutionResult:
    ok: bool
    plan: CairnPlan
    context: PlanExecutionContext
    primary_result: dict[str, Any] | None = None
    reason: str | None = None


def ready_steps(plan: CairnPlan, completed: set[str]) -> list[PlanStep]:
    ready: list[PlanStep] = []
    for step in plan.steps:
        if step.status != "pending":
            continue
        if all(dep in completed for dep in step.depends_on):
            ready.append(step)
    return ready


def interpret_plan(
    plan: CairnPlan,
    *,
    query: str,
    session_id: str,
    handlers: dict[str, StepHandler] | None = None,
    db: Any = None,
    persist_execution: bool = False,
    resume_execution: bool = True,
) -> PlanExecutionResult:
    """Walk *plan* step-by-step; return updated plan + execution context."""
    handlers = handlers or {}
    execution_id: str | None = None
    if db and persist_execution and resume_execution:
        from tirzah.planning.execution_store import load_plan_execution, resume_steps_and_context

        saved = load_plan_execution(db, plan.plan_id, plan.revision, session_id)
        if saved:
            working_steps, completed, artifacts, trace, effects = resume_steps_and_context(saved)
            context = PlanExecutionContext(
                query=query,
                session_id=session_id,
                artifacts=artifacts,
                trace=trace,
                effects=effects,
            )
            execution_id = saved.get("execution_id")
        else:
            context = PlanExecutionContext(query=query, session_id=session_id)
            working_steps = [replace(step) for step in plan.steps]
            completed = set()
    else:
        context = PlanExecutionContext(query=query, session_id=session_id)
        working_steps = [replace(step) for step in plan.steps]
        completed = set()
    max_rounds = len(working_steps) + 1

    for _ in range(max_rounds):
        ready = ready_steps(
            CairnPlan(
                plan_id=plan.plan_id,
                revision=plan.revision,
                parent_revision=plan.parent_revision,
                request=plan.request,
                trigger=plan.trigger,
                objective=plan.objective,
                status=plan.status,
                steps=working_steps,
            ),
            completed,
        )
        if not ready:
            break
        for step in ready:
            index = _step_index(working_steps, step.id)
            if index is None:
                continue
            working_steps[index] = replace(working_steps[index], status="active")
            _append_trace(context, step.id, "plan.step.started", {"construct": step.construct})
            outcome = _execute_step(working_steps[index], context, handlers)
            working_steps[index] = replace(
                working_steps[index],
                status=outcome["status"],
            )
            _append_trace(
                context,
                step.id,
                f"plan.step.{outcome['status']}",
                {"construct": step.construct, "reason": outcome.get("reason")},
            )
            if outcome["status"] == "completed":
                completed.add(step.id)
                if outcome.get("artifact") is not None:
                    context.artifacts[step.id] = outcome["artifact"]
            if db and persist_execution:
                from tirzah.planning.execution_store import save_plan_execution

                execution_id = save_plan_execution(
                    db,
                    plan=replace(plan, steps=working_steps),
                    session_id=session_id,
                    query=query,
                    steps=working_steps,
                    completed_step_ids=sorted(completed),
                    artifacts=_serializable_artifacts(context.artifacts),
                    trace=context.trace,
                    effects=sorted(context.effects),
                    status="running",
                    execution_id=execution_id,
                )

    updated = replace(plan, steps=working_steps)
    primary = (
        context.artifacts.get("synthesis_result")
        or context.artifacts.get("retrieval_result")
        or _first_artifact(context)
    )
    blocked = [s for s in working_steps if s.status == "blocked"]
    ok = not blocked and any(s.status == "completed" for s in working_steps)
    if db and persist_execution:
        from tirzah.planning.execution_store import finalize_plan_execution, save_plan_execution

        final_status = "completed" if ok else ("blocked" if blocked else "running")
        save_plan_execution(
            db,
            plan=updated,
            session_id=session_id,
            query=query,
            steps=working_steps,
            completed_step_ids=sorted(completed),
            artifacts=_serializable_artifacts(context.artifacts),
            trace=context.trace,
            effects=sorted(context.effects),
            status=final_status,
            execution_id=execution_id,
        )
        if final_status != "running":
            finalize_plan_execution(db, plan.plan_id, plan.revision, session_id, status=final_status)
    return PlanExecutionResult(
        ok=ok,
        plan=updated,
        context=context,
        primary_result=primary,
        reason=blocked[0].action if blocked else None,
    )


def build_default_handlers(
    *,
    pipeline_executor: Callable[..., dict[str, Any]] | None = None,
    db: Any,
    config: Any,
    answer_kwargs: dict[str, Any],
    specialist_runner: Callable[[CairnPlan, str, str], tuple[str | None, Any | None]] | None = None,
    use_split_phases: bool = True,
) -> dict[str, StepHandler]:
    """Registry for Tirzah's interpretive mode (split retrieve/synthesize by default)."""

    def tirzah_retrieval(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if "tirzah_retrieval" in ctx.effects:
            return {"ok": True, "skipped": True, "reason": "duplicate_effect"}
        if use_split_phases and pipeline_executor is None:
            from tirzah.sessions.answer_phases import retrieve_for_answer

            result = retrieve_for_answer(db, config, query=ctx.query, **answer_kwargs)
        else:
            executor = pipeline_executor
            if executor is None:
                return {"ok": False, "reason": "no_pipeline_executor"}
            result = executor(db, config, query=ctx.query, **answer_kwargs)
            ctx.effects.add("tirzah_retrieval")
            ctx.artifacts["synthesis_result"] = result
            return result
        ctx.effects.add("tirzah_retrieval")
        if not result.get("ok"):
            return result
        ctx.artifacts["retrieval_package"] = result["package"]
        return {
            "ok": True,
            "phase": "retrieval",
            "retrieval_status": (result.get("package") or {}).get("retrieval_status"),
        }

    def answer_adapter_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if ctx.artifacts.get("synthesis_result"):
            return ctx.artifacts["synthesis_result"]
        package = ctx.artifacts.get("retrieval_package")
        if package and use_split_phases and pipeline_executor is None:
            from tirzah.sessions.answer_phases import synthesize_from_retrieval

            result = synthesize_from_retrieval(db, config, package)
            ctx.artifacts["synthesis_result"] = result
            return result
        if pipeline_executor is not None:
            result = pipeline_executor(db, config, query=ctx.query, **answer_kwargs)
            ctx.artifacts["synthesis_result"] = result
            return result
        return {"ok": False, "reason": "missing_retrieval_package"}

    handlers: dict[str, StepHandler] = {
        "tirzah_retrieval": tirzah_retrieval,
        "answer_adapter": answer_adapter_handler,
    }

    if specialist_runner is not None:
        for tool in ("coherence_check", "coherence", "milcah", "specialist", "counter_framework", "research_specialist", "milcah_research"):
            handlers[tool] = _specialist_handler(specialist_runner, tool)

    return handlers


def _specialist_handler(
    runner: Callable[[CairnPlan, str, str], tuple[str | None, Any | None]],
    tool: str,
) -> StepHandler:
    def run(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        mini_plan = CairnPlan(
            plan_id="interpret",
            revision=1,
            parent_revision=None,
            request=ctx.query,
            trigger="interpret",
            objective=step.action,
            status="active",
            steps=[step],
        )
        mode, result = runner(mini_plan, ctx.query, ctx.session_id)
        if mode is None:
            return {"ok": False, "reason": "specialist_not_requested"}
        if result is None:
            return {"ok": False, "reason": "specialist_unavailable", "tool": tool}
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        return {"ok": True, "tool": tool, "mode": mode, "specialist": payload}

    return run


def _execute_step(
    step: PlanStep,
    context: PlanExecutionContext,
    handlers: dict[str, StepHandler],
) -> dict[str, Any]:
    construct = (step.construct or "STEP").upper()
    if construct == "STEP":
        return {"status": "completed", "artifact": {"acknowledged": True, "action": step.action}}
    if construct == "CALL":
        return _dispatch_call(step, context, handlers)
    if construct == "RECURSE":
        return {"status": "skipped", "reason": "revision_loop_owns_recursion"}
    if construct in {"ITERATE", "DECISION"}:
        return {"status": "blocked", "reason": f"construct_not_interpreted:{construct.lower()}"}
    return {"status": "blocked", "reason": f"unknown_construct:{construct}"}


def _dispatch_call(
    step: PlanStep,
    context: PlanExecutionContext,
    handlers: dict[str, StepHandler],
) -> dict[str, Any]:
    tools = [tool for tool in step.allowed_tools if tool in ALLOWED_PLAN_TOOLS]
    for tool in tools:
        handler = handlers.get(tool)
        if handler is None:
            continue
        try:
            artifact = handler(step, context)
        except Exception as error:
            return {"status": "blocked", "reason": "handler_failed", "error": str(error)}
        if isinstance(artifact, dict) and artifact.get("skipped"):
            return {"status": "skipped", "reason": artifact.get("reason", "duplicate_effect")}
        ok = True
        if isinstance(artifact, dict):
            ok = bool(artifact.get("ok", True))
        return {
            "status": "completed" if ok else "blocked",
            "artifact": artifact,
            "reason": None if ok else (artifact.get("reason") if isinstance(artifact, dict) else "handler_not_ok"),
        }
    return {"status": "blocked", "reason": "no_handler", "allowed_tools": tools}


def _append_trace(context: PlanExecutionContext, step_id: str, event: str, metadata: dict[str, Any]) -> None:
    context.trace.append({"step": event, "step_id": step_id, "metadata": metadata})


def _step_index(steps: list[PlanStep], step_id: str) -> int | None:
    for index, step in enumerate(steps):
        if step.id == step_id:
            return index
    return None


def _first_artifact(context: PlanExecutionContext) -> dict[str, Any] | None:
    for value in context.artifacts.values():
        if isinstance(value, dict):
            return value
    return None


def _serializable_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
    serializable: dict[str, Any] = {}
    for key, value in artifacts.items():
        if key in {"retrieval_package", "synthesis_result", "retrieval_result"} or isinstance(value, dict):
            serializable[key] = value
    return serializable