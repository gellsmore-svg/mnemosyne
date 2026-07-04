"""Interpretive Cairn PLAN execution (SPEC §4.6).

Walks a machine plan in dependency order, dispatches CALL steps through a handler
registry constrained by allowed_tools, and records per-step status + trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from tirzah.planning.constructs import (
    execute_decision_step,
    execute_iterate_step,
    is_owned_by_pending_parent,
)
from tirzah.planning.context_bundle import (
    append_tool_result,
    ensure_bundle,
    resolve_compile_node_id,
    resolve_document_id,
    resolve_focus_node_id,
    resolve_web_fetch_url,
)
from tirzah.planning.recursive import ALLOWED_PLAN_TOOLS, CairnPlan, PlanStep

StepHandler = Callable[[PlanStep, "PlanExecutionContext"], dict[str, Any]]


@dataclass
class PlanExecutionContext:
    query: str
    session_id: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    effects: set[str] = field(default_factory=set)  # once-only handlers (e.g. tirzah_retrieval)
    plan_steps: list[PlanStep] = field(default_factory=list)
    completed_step_ids: set[str] = field(default_factory=set)
    answer_kwargs: dict[str, Any] = field(default_factory=dict)
    config: Any = None
    iterate_round: int | None = None


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
        if is_owned_by_pending_parent(step, plan.steps, completed):
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
    config: Any = None,
    answer_kwargs: dict[str, Any] | None = None,
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
                config=config,
                answer_kwargs=dict(answer_kwargs or {}),
                plan_steps=working_steps,
                completed_step_ids=completed,
            )
            execution_id = saved.get("execution_id")
        else:
            context = PlanExecutionContext(
                query=query,
                session_id=session_id,
                config=config,
                answer_kwargs=dict(answer_kwargs or {}),
            )
            working_steps = [replace(step) for step in plan.steps]
            completed = set()
    else:
        context = PlanExecutionContext(
            query=query,
            session_id=session_id,
            config=config,
            answer_kwargs=dict(answer_kwargs or {}),
        )
        working_steps = [replace(step) for step in plan.steps]
        completed = set()
    context.plan_steps = working_steps
    context.completed_step_ids = completed
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
            outcome = _execute_step(working_steps[index], context, handlers, completed=completed)
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
        bundle = ctx.artifacts.get("context_bundle")
        if bundle and bundle.get("tool_results") and use_split_phases and pipeline_executor is None:
            from tirzah.sessions.answer_phases import synthesize_from_context_bundle

            bundle_kwargs = {key: value for key, value in answer_kwargs.items() if key != "session_id"}
            result = synthesize_from_context_bundle(
                db,
                config,
                query=ctx.query,
                session_id=ctx.session_id,
                bundle=bundle,
                **bundle_kwargs,
            )
            ctx.artifacts["synthesis_result"] = result
            return result
        if pipeline_executor is not None:
            result = pipeline_executor(db, config, query=ctx.query, **answer_kwargs)
            ctx.artifacts["synthesis_result"] = result
            return result
        return {"ok": False, "reason": "missing_retrieval_package"}

    def search_nodes_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        from tirzah.sessions import interaction as ix

        limit = 5
        output, details = ix.execute_search_nodes_tool(
            db,
            query=ctx.query,
            original_query=ctx.query,
            session_id=ctx.session_id,
            runtime_config=config.runtime if config is not None else None,
            limit=limit,
        )
        bundle = ensure_bundle(ctx.artifacts)
        entry = append_tool_result(
            bundle,
            tool="search_nodes",
            output=output,
            arguments={"query": ctx.query, "limit": limit},
            details=details,
        )
        return {"ok": True, "tool": "search_nodes", "tool_result": entry, "match_count": len(output.get("matches") or [])}

    def compile_context_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        bundle = ensure_bundle(ctx.artifacts)
        node_id = resolve_compile_node_id(bundle, answer_kwargs)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.retrieval.queries import compile_context

        context = compile_context(db, node_id)
        if not context:
            return {"ok": False, "reason": "node_not_found", "node_id": node_id}
        entry = append_tool_result(
            bundle,
            tool="compile_context",
            output=context,
            arguments={"node_id": node_id},
        )
        return {"ok": True, "tool": "compile_context", "tool_result": entry, "node_id": node_id}

    def web_search_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None or config is None:
            return {"ok": False, "reason": "missing_runtime"}
        if not config.runtime.web_research_enabled and not answer_kwargs.get("web_research"):
            return {"ok": False, "reason": "web_research_disabled"}
        from tirzah.sessions import interaction as ix
        from tirzah.web_research import sources_to_jsonable

        try:
            client = ix.make_web_research_client(config.runtime)
            sources = client.research(ctx.query)
        except Exception as error:
            return {"ok": False, "reason": "web_search_failed", "error": str(error)}
        output = {"query": ctx.query, "sources": sources_to_jsonable(sources)}
        bundle = ensure_bundle(ctx.artifacts)
        entry = append_tool_result(
            bundle,
            tool="web_search",
            output=output,
            arguments={"query": ctx.query},
        )
        return {"ok": True, "tool": "web_search", "tool_result": entry, "source_count": len(sources)}

    def web_fetch_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None or config is None:
            return {"ok": False, "reason": "missing_runtime"}
        if not config.runtime.web_research_enabled and not answer_kwargs.get("web_research"):
            return {"ok": False, "reason": "web_research_disabled"}
        bundle = ensure_bundle(ctx.artifacts)
        url = resolve_web_fetch_url(bundle)
        if not url:
            return {"ok": False, "reason": "missing_web_search_url"}
        from tirzah.sessions import interaction as ix

        try:
            client = ix.make_web_research_client(config.runtime)
            content = client.fetch(url)
        except Exception as error:
            return {"ok": False, "reason": "web_fetch_failed", "error": str(error)}
        output = {"url": url, "content": content, "untrusted": True}
        entry = append_tool_result(
            bundle,
            tool="web_fetch",
            output=output,
            arguments={"url": url},
        )
        return {"ok": True, "tool": "web_fetch", "tool_result": entry, "url": url}

    def _record_bundle_tool(
        ctx: PlanExecutionContext,
        *,
        tool: str,
        output: Any,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        bundle = ensure_bundle(ctx.artifacts)
        entry = append_tool_result(bundle, tool=tool, output=output, arguments=arguments)
        if isinstance(output, dict):
            count = len(output.get("matches") or output.get("nodes") or output.get("sources") or [])
        elif isinstance(output, list):
            count = len(output)
        else:
            count = 0
        return {"ok": True, "tool": tool, "tool_result": entry, "result_count": count}

    def _require_node_id(ctx: PlanExecutionContext) -> str | None:
        bundle = ensure_bundle(ctx.artifacts)
        return resolve_focus_node_id(bundle, answer_kwargs)

    def get_node_context_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        node_id = _require_node_id(ctx)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.retrieval.queries import node_context

        output = node_context(db, node_id, child_limit=5)
        return _record_bundle_tool(ctx, tool="get_node_context", output=output, arguments={"node_id": node_id})

    def get_graph_edges_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        node_id = _require_node_id(ctx)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.retrieval.queries import graph_edges_for_node

        output = graph_edges_for_node(db, node_id=node_id, direction="both", limit=5)
        return _record_bundle_tool(
            ctx,
            tool="get_graph_edges",
            output=output,
            arguments={"node_id": node_id, "direction": "both", "limit": 5},
        )

    def expand_proximity_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        node_id = _require_node_id(ctx)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.sessions import interaction as ix

        output = ix.execute_expand_proximity_tool(db, node_id=node_id, direction="both", limit=5)
        return _record_bundle_tool(
            ctx,
            tool="expand_proximity",
            output=output,
            arguments={"node_id": node_id, "direction": "both", "limit": 5},
        )

    def expand_graph_paths_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        node_id = _require_node_id(ctx)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.sessions import interaction as ix

        output = ix.execute_expand_graph_paths_tool(
            db, node_id=node_id, direction="both", max_depth=2, branch_limit=5, limit=5,
        )
        return _record_bundle_tool(
            ctx,
            tool="expand_graph_paths",
            output=output,
            arguments={"node_id": node_id, "direction": "both", "max_depth": 2, "limit": 5},
        )

    def semantic_candidates_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        node_id = _require_node_id(ctx)
        if not node_id:
            return {"ok": False, "reason": "missing_node_id"}
        from tirzah.sessions import interaction as ix

        output = ix.execute_semantic_candidates_tool(db, node_id=node_id, include_same_document=False, limit=5)
        return _record_bundle_tool(
            ctx,
            tool="semantic_candidates",
            output=output,
            arguments={"node_id": node_id, "include_same_document": False, "limit": 5},
        )

    def list_documents_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        from tirzah.retrieval.queries import list_documents

        output = list_documents(db, limit=5)
        return _record_bundle_tool(ctx, tool="list_documents", output=output, arguments={"limit": 5})

    def list_active_documents_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        from tirzah.sessions.active_documents import list_active_documents

        output = list_active_documents(db, session_id=ctx.session_id, limit=5)
        return _record_bundle_tool(
            ctx,
            tool="list_active_documents",
            output=output,
            arguments={"session_id": ctx.session_id, "limit": 5},
        )

    def get_document_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        bundle = ensure_bundle(ctx.artifacts)
        document_id = resolve_document_id(bundle, answer_kwargs)
        if not document_id:
            return {"ok": False, "reason": "missing_document_id"}
        from tirzah.retrieval.queries import get_document

        output = get_document(db, document_id)
        return _record_bundle_tool(ctx, tool="get_document", output=output, arguments={"document_id": document_id})

    def get_document_tree_handler(step: PlanStep, ctx: PlanExecutionContext) -> dict[str, Any]:
        if db is None:
            return {"ok": False, "reason": "missing_database"}
        bundle = ensure_bundle(ctx.artifacts)
        document_id = resolve_document_id(bundle, answer_kwargs)
        if not document_id:
            return {"ok": False, "reason": "missing_document_id"}
        from tirzah.db.repositories import document_tree

        output = document_tree(db, document_id)
        return _record_bundle_tool(ctx, tool="get_document_tree", output=output, arguments={"document_id": document_id})

    handlers: dict[str, StepHandler] = {
        "tirzah_retrieval": tirzah_retrieval,
        "answer_adapter": answer_adapter_handler,
        "search_nodes": search_nodes_handler,
        "compile_context": compile_context_handler,
        "get_node_context": get_node_context_handler,
        "get_graph_edges": get_graph_edges_handler,
        "expand_proximity": expand_proximity_handler,
        "expand_graph_paths": expand_graph_paths_handler,
        "semantic_candidates": semantic_candidates_handler,
        "list_documents": list_documents_handler,
        "list_active_documents": list_active_documents_handler,
        "get_document": get_document_handler,
        "get_document_tree": get_document_tree_handler,
        "web_search": web_search_handler,
        "web_fetch": web_fetch_handler,
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
    *,
    completed: set[str] | None = None,
) -> dict[str, Any]:
    completed = completed if completed is not None else context.completed_step_ids
    construct = (step.construct or "STEP").upper()
    if construct == "STEP":
        return {"status": "completed", "artifact": {"acknowledged": True, "action": step.action}}
    if construct == "CALL":
        return _dispatch_call(step, context, handlers)
    if construct == "RECURSE":
        return {"status": "skipped", "reason": "revision_loop_owns_recursion"}
    if construct == "ITERATE":
        def run_iterate_body(body_step: PlanStep, *, round_num: int = 1) -> dict[str, Any]:
            context.iterate_round = round_num
            return _run_iterate_body_step(body_step, context, handlers)

        return execute_iterate_step(
            step,
            steps=context.plan_steps,
            completed=completed,
            artifacts=context.artifacts,
            trace=context.trace,
            run_step=run_iterate_body,
        )
    if construct == "DECISION":
        return execute_decision_step(
            step,
            steps=context.plan_steps,
            completed=completed,
            artifacts=context.artifacts,
            answer_kwargs=context.answer_kwargs,
            config=context.config,
            trace=context.trace,
        )
    return {"status": "blocked", "reason": f"unknown_construct:{construct}"}


def _run_iterate_body_step(
    step: PlanStep,
    context: PlanExecutionContext,
    handlers: dict[str, StepHandler],
) -> dict[str, Any]:
    _append_trace(
        context,
        step.id,
        "plan.step.started",
        {"construct": step.construct, "round": context.iterate_round},
    )
    construct = (step.construct or "STEP").upper()
    if construct in {"BREAK", "CONTINUE"}:
        return {"status": "completed", "artifact": {"control": construct.lower()}}
    if construct == "DECISION":
        return execute_decision_step(
            step,
            steps=context.plan_steps,
            completed=context.completed_step_ids,
            artifacts=context.artifacts,
            answer_kwargs=context.answer_kwargs,
            config=context.config,
            trace=context.trace,
            branch_runner=lambda branch_step: _run_iterate_body_step(branch_step, context, handlers),
            round_num=context.iterate_round,
        )
    return _execute_step(
        step,
        context,
        handlers,
        completed=context.completed_step_ids,
    )


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
        if key in {"retrieval_package", "synthesis_result", "retrieval_result", "context_bundle"} or isinstance(value, dict):
            serializable[key] = value
    return serializable