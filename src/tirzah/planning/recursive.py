"""Versioned Cairn plans around Tirzah's existing request/answer pipeline.

The planner proposes and revises process state. Python validates structure, owns
execution and side effects, enforces revision/step bounds, and persists each
revision separately from trusted graph memory.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from tirzah.adapters.answer import answer_adapter
from tirzah.config import AppConfig, RuntimeConfig

PLAN_STATUSES = {"draft", "active", "stable", "complete", "blocked"}
STEP_STATUSES = {"pending", "active", "completed", "blocked", "skipped"}
CONSTRUCTS = {
    "STEP", "CALL", "ITERATE", "DECISION", "RECURSE", "PARALLEL", "MERGE", "BREAK", "CONTINUE", "RETRY", "ERROR",
}
REVISION_DECISIONS = {"revise", "stable", "complete", "blocked"}
ALLOWED_PLAN_TOOLS = {
    "tirzah_retrieval", "answer_adapter", "search_nodes", "compile_context",
    "get_node_context", "get_document", "get_document_tree", "get_graph_edges",
    "expand_proximity", "expand_graph_paths", "semantic_candidates",
    "list_active_documents", "list_documents", "web_search", "web_fetch",
}


@dataclass
class PlanStep:
    id: str
    action: str
    construct: str = "STEP"
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class CairnPlan:
    plan_id: str
    revision: int
    parent_revision: int | None
    request: str
    trigger: str
    objective: str
    status: str
    steps: list[PlanStep]
    stopping_conditions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    revision_decision: str = "revise"
    revision_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cairn_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["cairn_text"] = self.cairn_text or render_cairn_plan(self)
        return value


PlannerFn = Callable[[str], str]
ExecutorFn = Callable[..., dict[str, Any]]


def planner_runtime_config(runtime: RuntimeConfig) -> RuntimeConfig:
    planner = runtime.model_copy()
    planner.answer_adapter = runtime.planning_adapter or runtime.memory_agent_adapter or runtime.answer_adapter
    planner.ollama_model = runtime.planning_model or runtime.memory_agent_model or runtime.ollama_model
    planner.ollama_format = "json"
    planner.ollama_think = False
    return planner


def make_planner(runtime: RuntimeConfig) -> PlannerFn:
    planner_runtime = planner_runtime_config(runtime)

    def plan(prompt: str) -> str:
        result = answer_adapter(planner_runtime).answer(
            {"prompt_text": prompt, "context_text": "", "context_metadata": {"included": []}}
        )
        return str(result.get("answer") or "")

    return plan


def interpretive_plan_template_hint() -> str:
    return "\n".join(
        [
            "Preferred interpretive backbone (use unless the request clearly needs another shape):",
            '1. STEP — interpret the request and preserve constraints.',
            '2. CALL allowed_tools=["tirzah_retrieval"] depends_on=["1"] — gather memory context only.',
            '3. CALL allowed_tools=["answer_adapter"] depends_on=["2"] — synthesize a grounded answer.',
            '4. RECURSE depends_on=["3"] — revise when new evidence changes required work.',
            "The interpreter executes steps 2 and 3 separately; do not merge retrieval and synthesis.",
            "",
            "Granular context profile (when DB/web budget or revision scoping matters):",
            '2a. CALL allowed_tools=["search_nodes"] depends_on=["1"]',
            '2b. CALL allowed_tools=["compile_context"] depends_on=["2a"] (uses focus node or top search hit)',
            '2c. CALL allowed_tools=["expand_proximity"] or ["semantic_candidates"] depends_on=["2b"] — graph expansion',
            '2d. CALL allowed_tools=["web_search"] depends_on=["2c"] — only when external evidence is required',
            '2e. CALL allowed_tools=["web_fetch"] depends_on=["2d"] — optional when snippets are insufficient',
            '3. CALL allowed_tools=["answer_adapter"] depends_on=["2b" or "2e"] — synthesize from accumulated tool_results',
        ]
    )


def build_initial_plan_prompt(
    request: str,
    max_steps: int,
    context: str = "",
    *,
    profile_hint: str = "",
) -> str:
    lines = [
        "You are Tirzah's process planner immediately below the front end.",
        "Create a first-pass plan for the request. Do not answer or execute the request.",
        "Represent a Cairn PROCESS as strict JSON. Python will validate and execute it.",
        f"Use at most {max_steps} steps. Every loop or recursion must have an explicit bound.",
        "Allowed constructs: STEP, CALL, ITERATE, DECISION, RECURSE.",
        "Return only JSON with: objective, status, steps, stopping_conditions, unresolved_questions, revision_decision, revision_reason.",
        "Each step has: id, action, construct, status, depends_on, success_criteria, allowed_tools.",
        "status is active; revision_decision is revise unless the request is already complete or blocked.",
        "Do not claim tools were executed. Do not grant tools or side effects not stated by the request/runtime.",
        "",
        interpretive_plan_template_hint(),
        "",
    ]
    if profile_hint:
        lines += [profile_hint, ""]
    if context:
        # Phase 5: make planning context-aware with prior decisions/constraints/open items.
        lines += [context, ""]
    lines += ["User request:", request]
    return "\n".join(lines)


def build_revision_prompt(plan: CairnPlan, new_information: dict[str, Any], max_steps: int) -> str:
    return "\n".join([
        "You are Tirzah's recursive process planner.",
        "Revise the plan only where the new information requires it. Do not execute the request.",
        "Return a complete replacement Cairn PROCESS as strict JSON, not a patch.",
        f"Use at most {max_steps} steps. Preserve hard bounds, permissions, and completed work.",
        "revision_decision must be revise, stable, complete, or blocked.",
        "Return only JSON with: objective, status, steps, stopping_conditions, unresolved_questions, revision_decision, revision_reason.",
        "Each step has: id, action, construct, status, depends_on, success_criteria, allowed_tools.",
        "Preserve separate tirzah_retrieval and answer_adapter CALL steps when execution is interpretive.",
        "",
        interpretive_plan_template_hint(),
        "",
        "Current plan:",
        json.dumps(plan.to_dict(), indent=2, default=str),
        "",
        "New information:",
        json.dumps(new_information, indent=2, default=str),
    ])


def create_initial_plan(
    request: str,
    *,
    planner: PlannerFn,
    max_steps: int = 12,
    context: str = "",
    profile_hint: str = "",
) -> CairnPlan:
    plan_id = f"plan_{uuid4().hex}"
    try:
        payload = parse_plan_payload(
            planner(build_initial_plan_prompt(request, max_steps, context, profile_hint=profile_hint))
        )
        return plan_from_payload(
            payload, plan_id=plan_id, revision=1, parent_revision=None,
            request=request, trigger="initial_request", max_steps=max_steps,
        )
    except Exception as error:
        # Includes a parseable plan with no valid steps — fall back, don't 500.
        return fallback_plan(request, plan_id=plan_id, reason=f"planner fallback: {error}", max_steps=max_steps)


def revise_plan(
    plan: CairnPlan,
    new_information: dict[str, Any],
    *,
    planner: PlannerFn,
    max_steps: int = 12,
) -> CairnPlan:
    revision = plan.revision + 1
    trigger = compact_trigger(new_information)
    try:
        payload = parse_plan_payload(planner(build_revision_prompt(plan, new_information, max_steps)))
        return plan_from_payload(
            payload, plan_id=plan.plan_id, revision=revision,
            parent_revision=plan.revision, request=plan.request,
            trigger=trigger, max_steps=max_steps,
        )
    except Exception as error:
        # Keep the prior plan stable rather than failing the whole request.
        payload = {
            "objective": plan.objective,
            "status": "stable",
            "steps": [asdict(step) for step in plan.steps],
            "stopping_conditions": plan.stopping_conditions,
            "unresolved_questions": plan.unresolved_questions,
            "revision_decision": "stable",
            "revision_reason": f"revision planner fallback: {error}",
        }
        try:
            return plan_from_payload(
                payload, plan_id=plan.plan_id, revision=revision,
                parent_revision=plan.revision, request=plan.request,
                trigger=trigger, max_steps=max_steps,
            )
        except Exception:
            return fallback_plan(plan.request, plan_id=plan.plan_id, reason=f"revision fallback: {error}", max_steps=max_steps)


def revise_plan_recursively(
    plan: CairnPlan,
    information_batches: Iterable[dict[str, Any]],
    *,
    planner: PlannerFn,
    max_revisions: int = 3,
    max_steps: int = 12,
) -> list[CairnPlan]:
    revisions = [plan]
    for information in information_batches:
        if len(revisions) >= max(1, max_revisions):
            break
        current = revise_plan(revisions[-1], information, planner=planner, max_steps=max_steps)
        revisions.append(current)
        if current.revision_decision in {"stable", "complete", "blocked"}:
            break
    return revisions


def _interpretive_result_from_execution(
    execution: Any,
    plan: CairnPlan,
    *,
    db: Any,
    query: str,
    session_id: str,
) -> dict[str, Any]:
    from tirzah.planning.context_bundle import compact_context_bundle_summary
    from tirzah.planning.execution_store import compact_execution_summary, get_plan_execution

    result = execution.primary_result or {
        "ok": execution.ok,
        "reason": execution.reason or "plan_interpretation_incomplete",
        "query": query,
        "session_id": session_id,
    }
    process_trace: list[dict[str, Any]] = list(execution.context.trace or [])
    bundle = execution.context.artifacts.get("context_bundle")
    if bundle:
        result["context_bundle_summary"] = compact_context_bundle_summary(bundle)
        process_trace.append(
            {
                "step": "context_bundle",
                "input": {"session_id": session_id},
                "output": result["context_bundle_summary"],
            }
        )
    saved_execution = get_plan_execution(db, plan.plan_id, plan.revision, session_id)
    if saved_execution:
        result["plan_execution"] = compact_execution_summary(saved_execution)
        process_trace.append(
            {
                "step": "plan_execution",
                "input": {"plan_id": plan.plan_id, "revision": plan.revision},
                "output": result["plan_execution"],
            }
        )
    if process_trace:
        result["process_trace"] = process_trace
    return result


def _merge_interpretive_results(previous: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    merged = {**previous, **fresh}
    merged["process_trace"] = (previous.get("process_trace") or []) + (fresh.get("process_trace") or [])
    return merged


def _revision_trace_event(step: str, plan: CairnPlan) -> dict[str, Any]:
    return {
        "step": step,
        "input": {
            "plan_id": plan.plan_id,
            "revision": plan.revision,
            "parent_revision": plan.parent_revision,
        },
        "output": {
            "revision_decision": plan.revision_decision,
            "revision_reason": plan.revision_reason,
            "status": plan.status,
            "step_count": len(plan.steps),
        },
    }


def process_frontend_request(
    db,
    config: AppConfig,
    *,
    query: str,
    executor: ExecutorFn,
    planner: PlannerFn | None = None,
    planning_enabled: bool | None = None,
    **answer_kwargs: Any,
) -> dict[str, Any]:
    enabled = config.runtime.recursive_planning_enabled if planning_enabled is None else planning_enabled
    if not enabled:
        return executor(db, config, query=query, **answer_kwargs)
    planner = planner or make_planner(config.runtime)
    # Phase 5: make planning context-aware with relevant prior decisions/open items.
    planning_context = ""
    try:
        from tirzah.sessions.interaction import render_planning_context

        planning_context = render_planning_context(db, config, answer_kwargs.get("session_id"), query)
    except Exception:
        planning_context = ""
    # Advertise enabled specialist tools to the planner, sourced from the Keturah
    # manifest (single source of truth) rather than a hardcoded string.
    try:
        from tirzah.manifest import render_planner_tool_hint

        tool_hint = render_planner_tool_hint(config.runtime)
        if tool_hint:
            planning_context = (planning_context + "\n\n" + tool_hint).strip()
    except Exception:
        pass
    profile_hint = ""
    try:
        from tirzah.planning.constructs import suggest_plan_profile_hint

        profile_hint = suggest_plan_profile_hint(query, answer_kwargs, config)
    except Exception:
        profile_hint = ""
    initial = create_initial_plan(
        query,
        planner=planner,
        max_steps=config.runtime.planning_max_steps,
        context=planning_context,
        profile_hint=profile_hint,
    )
    session_id = answer_kwargs.get("session_id", "web")
    save_plan_revision(db, initial, session_id=session_id)
    interpretive = config.runtime.plan_interpretive_execution_enabled
    revisions: list[CairnPlan] = []
    if interpretive:
        from tirzah.coherence import make_client, run_planned_specialist
        from tirzah.planning.executor import build_default_handlers, interpret_plan

        handlers = build_default_handlers(
            db=db,
            config=config,
            answer_kwargs=answer_kwargs,
            specialist_runner=lambda plan, q, sid: run_planned_specialist(
                plan, q, client=make_client(config.runtime), session_id=sid
            ),
            use_split_phases=True,
        )
        execution = interpret_plan(
            initial,
            query=query,
            session_id=session_id,
            handlers=handlers,
            db=db,
            config=config,
            answer_kwargs=answer_kwargs,
            persist_execution=True,
            resume_execution=True,
        )
        current_plan = execution.plan
        save_plan_revision(db, current_plan, session_id=session_id)
        result = _interpretive_result_from_execution(
            execution, current_plan, db=db, query=query, session_id=session_id
        )
        revisions = [current_plan]
        information = information_from_result(result)
        while len(revisions) < max(1, config.runtime.planning_max_revisions):
            proposed = revise_plan(
                revisions[-1],
                information,
                planner=planner,
                max_steps=config.runtime.planning_max_steps,
            )
            if proposed.revision <= revisions[-1].revision:
                break
            revisions.append(proposed)
            save_plan_revision(db, proposed, session_id=session_id)
            result.setdefault("process_trace", []).append(_revision_trace_event("plan.revision.proposed", proposed))
            if proposed.revision_decision in {"stable", "complete", "blocked"}:
                break
            execution = interpret_plan(
                proposed,
                query=query,
                session_id=session_id,
                handlers=handlers,
                db=db,
                config=config,
                answer_kwargs=answer_kwargs,
                persist_execution=True,
                resume_execution=False,
            )
            current_plan = execution.plan
            revisions[-1] = current_plan
            save_plan_revision(db, current_plan, session_id=session_id)
            fresh = _interpretive_result_from_execution(
                execution, current_plan, db=db, query=query, session_id=session_id
            )
            result = _merge_interpretive_results(result, fresh)
            result.setdefault("process_trace", []).append(
                _revision_trace_event("plan.revision.executed", current_plan)
            )
            information = information_from_result(result)
        initial = revisions[-1]
    else:
        result = executor(db, config, query=query, **answer_kwargs)
        information = information_from_result(result)
        revisions = revise_plan_recursively(
            initial,
            [information],
            planner=planner,
            max_revisions=config.runtime.planning_max_revisions,
            max_steps=config.runtime.planning_max_steps,
        )
        for revision in revisions[1:]:
            save_plan_revision(db, revision, session_id=session_id)
        initial = revisions[-1]
    plan_trace = [
        {
            "step": "request_plan",
            "input": {"request": query, "revision": revision.revision, "trigger": revision.trigger},
            "output": {
                "plan_id": revision.plan_id,
                "status": revision.status,
                "revision_decision": revision.revision_decision,
                "revision_reason": revision.revision_reason,
            },
        }
        for revision in revisions
    ]
    result["process_trace"] = plan_trace[:1] + (result.get("process_trace") or []) + plan_trace[1:]
    # Trigger: if the plan derived a specialist (coherence/research) need, invoke Milcah.
    try:
        from tirzah.coherence import make_client, run_planned_specialist

        mode, specialist = run_planned_specialist(
            initial, query, client=make_client(config.runtime), session_id=answer_kwargs.get("session_id")
        )
        if mode is not None and specialist is not None:
            result["specialist"] = specialist.to_dict()
            result["process_trace"].append(
                {
                    "step": "specialist_coherence",
                    "input": {"mode": mode, "query": query},
                    "output": {
                        "claims": len(specialist.claims),
                        "objections": len(specialist.objections),
                        "confidence": specialist.confidence,
                        "terminal_reason": specialist.terminal_reason,
                    },
                }
            )
    except Exception:
        pass
    result["request_plan"] = revisions[-1].to_dict()
    result["plan_revisions"] = [revision.to_dict() for revision in revisions]
    if result.get("activity_report"):
        result["activity_report"]["request_plan"] = compact_plan_summary(revisions[-1])
    existing_log = result.get("activity_log") or ""
    result["activity_log"] = render_plan_activity(revisions) + ("\n\n" + existing_log if existing_log else "")
    return result


def render_plan_activity(revisions: list[CairnPlan]) -> str:
    final = revisions[-1]
    lines = [
        "Request Plan",
        f"- Plan: {final.plan_id}",
        f"- Revisions: {len(revisions)}",
        f"- Status: {final.status}",
        f"- Decision: {final.revision_decision}",
        f"- Steps: {len(final.steps)}",
    ]
    if final.revision_reason:
        lines.append(f"- Reason: {final.revision_reason}")
    return "\n".join(lines)


def parse_plan_payload(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner did not return a JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("planner JSON must be an object")
    return value


def plan_from_payload(
    payload: dict[str, Any], *, plan_id: str, revision: int,
    parent_revision: int | None, request: str, trigger: str, max_steps: int,
) -> CairnPlan:
    steps = normalize_steps(payload.get("steps"), max_steps=max_steps)
    if not steps:
        raise ValueError("plan requires at least one valid step")
    decision = clean_choice(payload.get("revision_decision"), REVISION_DECISIONS, "revise")
    status_default = "stable" if decision == "stable" else (decision if decision in {"complete", "blocked"} else "active")
    status = clean_choice(payload.get("status"), PLAN_STATUSES, status_default)
    if decision in {"stable", "complete", "blocked"}:
        status = decision
    plan = CairnPlan(
        plan_id=plan_id,
        revision=revision,
        parent_revision=parent_revision,
        request=request[:4000],
        trigger=trigger[:2000],
        objective=clean_text(payload.get("objective") or request, 2000),
        status=status,
        steps=steps,
        stopping_conditions=clean_list(payload.get("stopping_conditions"), 12, 500),
        unresolved_questions=clean_list(payload.get("unresolved_questions"), 12, 500),
        revision_decision=decision,
        revision_reason=clean_text(payload.get("revision_reason"), 1000),
    )
    for step in plan.steps:
        step.status = "pending"
    plan.steps = ensure_interpretive_plan_shape(plan.steps)
    plan.cairn_text = render_cairn_plan(plan)
    return plan


def ensure_interpretive_plan_shape(steps: list[PlanStep]) -> list[PlanStep]:
    """Insert a synthesis CALL when retrieval is planned without answer_adapter."""
    retrieval_steps = [step for step in steps if "tirzah_retrieval" in step.allowed_tools]
    synthesis_steps = [step for step in steps if "answer_adapter" in step.allowed_tools]
    if not retrieval_steps or synthesis_steps:
        return steps
    retrieval = retrieval_steps[-1]
    new_id = _next_step_id(steps)
    synthesis = PlanStep(
        id=new_id,
        action="Synthesize a grounded answer from the retrieved context package.",
        construct="CALL",
        status="pending",
        depends_on=[retrieval.id],
        allowed_tools=["answer_adapter"],
        success_criteria=["Answer cites gathered context."],
    )
    updated = list(steps)
    insert_at = updated.index(retrieval) + 1
    updated.insert(insert_at, synthesis)
    for step in updated:
        if step.construct == "RECURSE" and retrieval.id in step.depends_on and new_id not in step.depends_on:
            step.depends_on = [new_id if dep == retrieval.id else dep for dep in step.depends_on]
            if retrieval.id in step.depends_on:
                step.depends_on = [dep for dep in step.depends_on if dep != retrieval.id] + [new_id]
    recurse_steps = [step for step in updated if step.construct == "RECURSE"]
    for recurse in recurse_steps:
        if retrieval.id in recurse.depends_on and new_id not in recurse.depends_on:
            recurse.depends_on = [new_id if dep == retrieval.id else dep for dep in recurse.depends_on]
    return updated


def _next_step_id(steps: list[PlanStep]) -> str:
    numeric = [int(step.id) for step in steps if str(step.id).isdigit()]
    return str((max(numeric) if numeric else len(steps)) + 1)


def normalize_steps(value: Any, *, max_steps: int) -> list[PlanStep]:
    if not isinstance(value, list):
        return []
    steps = []
    seen = set()
    for index, item in enumerate(value[: max(1, min(max_steps, 30))], 1):
        if not isinstance(item, dict):
            continue
        step_id = clean_text(item.get("id") or str(index), 40)
        action = clean_text(item.get("action"), 1000)
        if not action or step_id in seen:
            continue
        seen.add(step_id)
        steps.append(PlanStep(
            id=step_id,
            action=action,
            construct=clean_choice(str(item.get("construct") or "STEP").upper(), CONSTRUCTS, "STEP"),
            status=clean_choice(item.get("status"), STEP_STATUSES, "pending"),
            depends_on=clean_list(item.get("depends_on"), 12, 40),
            success_criteria=clean_list(item.get("success_criteria"), 8, 300),
            allowed_tools=clean_list(item.get("allowed_tools"), 12, 80),
        ))
    valid_ids = {step.id for step in steps}
    for step in steps:
        step.depends_on = [value for value in step.depends_on if value in valid_ids and value != step.id]
        step.allowed_tools = [value for value in step.allowed_tools if value in ALLOWED_PLAN_TOOLS]
    return steps


def fallback_plan(request: str, *, plan_id: str, reason: str, max_steps: int = 12) -> CairnPlan:
    payload = {
        "objective": request,
        "status": "active",
        "steps": [
            {"id": "1", "construct": "STEP", "action": "Interpret the request and preserve its constraints.", "success_criteria": ["Intent and boundaries are explicit."]},
            {"id": "2", "construct": "CALL", "action": "Gather relevant repository or web evidence through Tirzah's validated retrieval pipeline.", "depends_on": ["1"], "allowed_tools": ["tirzah_retrieval"]},
            {
                "id": "3",
                "construct": "CALL",
                "action": "Synthesize a grounded answer from the retrieved context package.",
                "depends_on": ["2"],
                "allowed_tools": ["answer_adapter"],
            },
            {"id": "4", "construct": "RECURSE", "action": "Evaluate new evidence and revise this plan when it changes required work.", "depends_on": ["3"], "success_criteria": ["Stop when stable, complete, blocked, or at the revision limit."]},
        ],
        "stopping_conditions": ["The request is complete.", "The plan is stable.", "Required authority or information is unavailable.", "The configured revision limit is reached."],
        "revision_decision": "revise",
        "revision_reason": reason,
    }
    return plan_from_payload(payload, plan_id=plan_id, revision=1, parent_revision=None, request=request, trigger="initial_request", max_steps=max_steps)


def render_cairn_plan(plan: CairnPlan) -> str:
    lines = [
        f"PLAN {plan.plan_id} REVISION {plan.revision} [STATUS: {plan.status}]",
        f"  PARENT: {plan.parent_revision if plan.parent_revision is not None else 'none'}",
        f"  REQUEST: {plan.request}",
        f"  TRIGGER: {plan.trigger}",
        "",
        "  PROCESS FulfilRequest (INPUT: user_request; OUTPUT: result, process_trace)",
        f"    PURPOSE: {plan.objective}",
    ]
    for step in plan.steps:
        tags = [step.construct, step.status.upper()]
        lines.append(f"    {step.id}. {step.action} [{', '.join(tags)}]")
        if step.depends_on:
            lines.append(f"       CONTEXT: depends on {', '.join(step.depends_on)}")
        if step.allowed_tools:
            lines.append(f"       CONSTRAINTS: allowed tools: {', '.join(step.allowed_tools)}")
        if step.success_criteria:
            lines.append(f"       OUTPUT: {'; '.join(step.success_criteria)}")
    if plan.stopping_conditions:
        lines.append(f"    CONSTRAINTS: stop when {'; '.join(plan.stopping_conditions)}")
    if plan.unresolved_questions:
        lines.append(f"    RISKS: unresolved: {'; '.join(plan.unresolved_questions)}")
    lines.append("  OUTPUT: result, process_trace")
    return "\n".join(lines)


def save_plan_revision(db, plan: CairnPlan, *, session_id: str) -> None:
    collection = getattr(db, "recursive_plans", None)
    if collection is None:
        return
    row = plan.to_dict()
    row["session_id"] = session_id
    row["created_at"] = datetime.now(timezone.utc)
    try:
        collection.insert_one(row)
    except Exception:
        return


def list_plan_revisions(db, plan_id: str, limit: int = 20) -> list[dict[str, Any]]:
    collection = getattr(db, "recursive_plans", None)
    if collection is None:
        return []
    rows = collection.find({"plan_id": plan_id}, {"_id": 0}).sort("revision", 1).limit(max(1, min(limit, 100)))
    return [serialize_value(row) for row in rows]


def latest_plan(db, plan_id: str) -> CairnPlan | None:
    revisions = list_plan_revisions(db, plan_id, limit=100)
    if not revisions:
        return None
    return plan_from_record(revisions[-1])


def revise_saved_plan(
    db,
    config: AppConfig,
    *,
    plan_id: str,
    new_information: dict[str, Any],
    planner: PlannerFn | None = None,
    session_id: str = "web",
) -> CairnPlan:
    current = latest_plan(db, plan_id)
    if current is None:
        raise ValueError(f"Unknown plan: {plan_id}")
    if current.revision >= config.runtime.planning_max_revisions:
        raise ValueError("Plan revision limit reached.")
    revised = revise_plan(
        current,
        new_information,
        planner=planner or make_planner(config.runtime),
        max_steps=config.runtime.planning_max_steps,
    )
    save_plan_revision(db, revised, session_id=session_id)
    return revised


def plan_from_record(value: dict[str, Any]) -> CairnPlan:
    return CairnPlan(
        plan_id=str(value["plan_id"]),
        revision=int(value["revision"]),
        parent_revision=value.get("parent_revision"),
        request=str(value.get("request") or ""),
        trigger=str(value.get("trigger") or ""),
        objective=str(value.get("objective") or ""),
        status=str(value.get("status") or "active"),
        steps=[PlanStep(**step) for step in value.get("steps") or []],
        stopping_conditions=list(value.get("stopping_conditions") or []),
        unresolved_questions=list(value.get("unresolved_questions") or []),
        revision_decision=str(value.get("revision_decision") or "revise"),
        revision_reason=str(value.get("revision_reason") or ""),
        created_at=str(value.get("created_at") or ""),
        cairn_text=str(value.get("cairn_text") or ""),
    )


def serialize_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items() if key != "_id"}
    return value


def information_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "reason": result.get("reason"),
        "retrieval_status": result.get("retrieval_status"),
        "controller_decision": result.get("controller_decision"),
        "used_node_ids": result.get("used_node_ids") or [],
        "answer_preview": clean_text(result.get("answer") or result.get("message"), 1500),
        "evidence_summary": ((result.get("activity_report") or {}).get("context_construction") or {}).get("evidence_summary"),
    }


def compact_trigger(value: dict[str, Any]) -> str:
    return clean_text(json.dumps(value, default=str, sort_keys=True), 2000)


def compact_plan_summary(plan: CairnPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "status": plan.status,
        "objective": plan.objective,
        "revision_decision": plan.revision_decision,
        "revision_reason": plan.revision_reason,
        "step_count": len(plan.steps),
        "unresolved_questions": plan.unresolved_questions,
    }


def clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_list(value: Any, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(item, item_limit) for item in value[:limit] if clean_text(item, item_limit)]


def clean_choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or "").strip().lower()
    upper_allowed = {item.upper() for item in allowed}
    if candidate.upper() in upper_allowed:
        for item in allowed:
            if item.upper() == candidate.upper():
                return item
    return default
