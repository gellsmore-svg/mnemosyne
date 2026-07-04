"""Split answer pipeline into retrieval and synthesis phases for interpretive PLAN execution.

``retrieve_for_answer`` gathers context (and may pre-synthesize in deep mode).
``synthesize_from_retrieval`` runs the answer adapter and persists the exchange.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pymongo.database import Database

from tirzah.adapters.answer import answer_adapter
from tirzah.config import AppConfig, RuntimeConfig
from tirzah.domains.registry import (
    clean_domain_id,
    conversation_domain_id_for_session,
)
from tirzah.retrieval.deep import run_deep_answer
from tirzah.retrieval.queries import node_identity
from tirzah.sessions.interaction import (
    answer_exception_payload,
    attach_answer_activity,
    build_agentic_answer_envelope,
    final_context_proposal_from_trace,
    final_controller_decision_from_trace,
    finish_answer_process_run,
    first_active_agent_identity,
    inject_history_into_prompt,
    is_low_intent_query,
    prepare_direct_answer_prompt,
    render_session_history_block,
    run_memory_agent_loop,
    schedule_chunking,
    schedule_turn_embedding,
    start_answer_process_run,
)
from tirzah.sessions.exchanges import save_exchange


@dataclass
class AnswerRetrievalPackage:
    query: str
    session_id: str
    focus_node_id: str | None
    selected_node_id: str | None
    retrieval_mode: str
    runtime_config: dict[str, Any]
    process_trace: list[dict[str, Any]] = field(default_factory=list)
    process_run_id: str | None = None
    project_domain_id: str | None = None
    conversation_domain_id: str | None = None
    prompt: dict[str, Any] | None = None
    retrieval_status: str | None = None
    controller_decision: Any = None
    pre_built_answer: dict[str, Any] | None = None
    deep_trace: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "session_id": self.session_id,
            "focus_node_id": self.focus_node_id,
            "selected_node_id": self.selected_node_id,
            "retrieval_mode": self.retrieval_mode,
            "runtime_config": self.runtime_config,
            "process_trace": self.process_trace,
            "process_run_id": self.process_run_id,
            "project_domain_id": self.project_domain_id,
            "conversation_domain_id": self.conversation_domain_id,
            "prompt": self.prompt,
            "retrieval_status": self.retrieval_status,
            "controller_decision": self.controller_decision,
            "pre_built_answer": self.pre_built_answer,
            "deep_trace": self.deep_trace,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnswerRetrievalPackage":
        return cls(
            query=str(value.get("query") or ""),
            session_id=str(value.get("session_id") or "default"),
            focus_node_id=value.get("focus_node_id"),
            selected_node_id=value.get("selected_node_id"),
            retrieval_mode=str(value.get("retrieval_mode") or "direct"),
            runtime_config=dict(value.get("runtime_config") or {}),
            process_trace=list(value.get("process_trace") or []),
            process_run_id=value.get("process_run_id"),
            project_domain_id=value.get("project_domain_id"),
            conversation_domain_id=value.get("conversation_domain_id"),
            prompt=value.get("prompt"),
            retrieval_status=value.get("retrieval_status"),
            controller_decision=value.get("controller_decision"),
            pre_built_answer=value.get("pre_built_answer"),
            deep_trace=value.get("deep_trace"),
        )


def retrieve_for_answer(
    db: Database,
    config: AppConfig,
    query: str,
    focus_node_id: str | None = None,
    session_id: str = "default",
    answer_adapter_name: str | None = None,
    ollama_model: str | None = None,
    retrieval_mode: str | None = None,
    project_domain_id: str | None = None,
    conversation_domain_id: str | None = None,
    web_research: bool | None = None,
) -> dict[str, Any]:
    """Retrieval phase only — no answer adapter, no exchange persist."""
    runtime_config, process_trace, process_run_id = _begin_answer_request(
        db,
        config,
        query=query,
        focus_node_id=focus_node_id,
        session_id=session_id,
        answer_adapter_name=answer_adapter_name,
        ollama_model=ollama_model,
        retrieval_mode=retrieval_mode,
        web_research=web_research,
    )
    package = AnswerRetrievalPackage(
        query=query,
        session_id=session_id,
        focus_node_id=focus_node_id,
        selected_node_id=focus_node_id,
        retrieval_mode=runtime_config.retrieval_mode,
        runtime_config=runtime_config.model_dump(),
        process_trace=process_trace,
        process_run_id=process_run_id,
        project_domain_id=project_domain_id,
        conversation_domain_id=conversation_domain_id,
    )
    if runtime_config.retrieval_mode == "deep":
        return _retrieve_deep(db, config, runtime_config, package)
    if runtime_config.retrieval_mode == "agentic":
        return _retrieve_agentic(db, config, runtime_config, package)
    return _retrieve_direct(db, config, package)


def synthesize_from_retrieval(
    db: Database,
    config: AppConfig,
    package: AnswerRetrievalPackage | dict[str, Any],
) -> dict[str, Any]:
    """Synthesis + persist phase using a retrieval package."""
    if isinstance(package, dict):
        package = AnswerRetrievalPackage.from_dict(package)
    runtime_config = RuntimeConfig.model_validate(package.runtime_config)
    if package.pre_built_answer:
        return _persist_prebuilt_answer(db, config, runtime_config, package)
    return _synthesize_and_persist(db, config, runtime_config, package)


def _begin_answer_request(
    db: Database,
    config: AppConfig,
    *,
    query: str,
    focus_node_id: str | None,
    session_id: str,
    answer_adapter_name: str | None,
    ollama_model: str | None,
    retrieval_mode: str | None,
    web_research: bool | None,
) -> tuple[RuntimeConfig, list[dict[str, Any]], str | None]:
    process_trace: list[dict[str, Any]] = [
        {
            "step": "user_prompt",
            "input": {
                "query": query,
                "focus_node_id": focus_node_id,
                "session_id": session_id,
                "requested_adapter": answer_adapter_name,
                "requested_model": ollama_model,
                "retrieval_mode": retrieval_mode or config.runtime.retrieval_mode,
            },
            "output": {"submitted_prompt": query},
        }
    ]
    runtime_config = config.runtime.model_copy()
    if answer_adapter_name:
        runtime_config.answer_adapter = answer_adapter_name
    if ollama_model:
        runtime_config.ollama_model = ollama_model
    if retrieval_mode:
        runtime_config.retrieval_mode = retrieval_mode
    if web_research is not None:
        runtime_config.web_research_enabled = web_research
    if web_research and runtime_config.retrieval_mode != "agentic":
        runtime_config.retrieval_mode = "agentic"
        process_trace[0]["output"]["retrieval_mode_override"] = "agentic_for_web_research"
    if runtime_config.retrieval_mode == "agentic" and not focus_node_id and is_low_intent_query(query):
        runtime_config.retrieval_mode = "direct"
        process_trace[0]["output"]["retrieval_mode_override"] = "direct_no_context_low_intent"
    process_run_id = start_answer_process_run(
        db,
        session_id=session_id,
        retrieval_mode=runtime_config.retrieval_mode,
    )
    return runtime_config, process_trace, process_run_id


def _retrieve_direct(db: Database, config: AppConfig, package: AnswerRetrievalPackage) -> dict[str, Any]:
    try:
        preparation = prepare_direct_answer_prompt(
            db,
            config=config,
            query=package.query,
            focus_node_id=package.focus_node_id,
            session_id=package.session_id,
        )
    except Exception as error:
        return _retrieval_failed(db, package, "retrieval_failed", "retrieval_context", error, config.runtime)
    package.selected_node_id = preparation["selected_node_id"]
    package.prompt = preparation["prompt"]
    package.retrieval_status = preparation["retrieval_status"]
    package.controller_decision = preparation["prompt"]["context_metadata"].get("controller_decision")
    package.process_trace.append(
        {
            "step": "retrieval_context",
            "input": {
                "query": package.query,
                "provided_focus_node_id": package.focus_node_id,
                "selected_node_id": package.selected_node_id,
                "selected_node_source": preparation["selected_node_source"],
                "mode": package.retrieval_mode,
            },
            "output": preparation["retrieval_output"],
        }
    )
    return {"ok": True, "package": package.to_dict(), "phase": "retrieval"}


def _retrieve_agentic(
    db: Database,
    config: AppConfig,
    runtime_config: RuntimeConfig,
    package: AnswerRetrievalPackage,
) -> dict[str, Any]:
    try:
        tool_results = run_memory_agent_loop(
            db=db,
            runtime_config=runtime_config,
            query=package.query,
            focus_node_id=package.focus_node_id,
            session_id=package.session_id,
            max_iterations=config.retrieval.memory_agent_max_iterations,
            process_trace=package.process_trace,
        )
        prompt = build_agentic_answer_envelope(
            query=package.query,
            tool_results=tool_results,
            token_budget=config.retrieval.prompt_token_budget,
            reserved_response_tokens=config.retrieval.reserved_response_tokens,
            proposed_controller_decision=final_controller_decision_from_trace(package.process_trace),
            context_proposal=final_context_proposal_from_trace(package.process_trace),
        )
        prompt = inject_history_into_prompt(
            prompt,
            render_session_history_block(db, config, session_id=package.session_id, query=package.query),
        )
    except Exception as error:
        return _retrieval_failed(
            db, package, "agentic_retrieval_failed", "memory_agent_iteration", error, runtime_config
        )
    package.prompt = prompt
    package.retrieval_status = prompt["context_metadata"]["retrieval_status"]
    package.controller_decision = prompt["context_metadata"].get("controller_decision")
    package.selected_node_id = package.focus_node_id
    return {"ok": True, "package": package.to_dict(), "phase": "retrieval"}


def _retrieve_deep(
    db: Database,
    config: AppConfig,
    runtime_config: RuntimeConfig,
    package: AnswerRetrievalPackage,
) -> dict[str, Any]:
    identity = first_active_agent_identity(db) if package.session_id else None
    try:
        deep_result = run_deep_answer(
            db,
            package.query,
            config=config,
            runtime_config=runtime_config,
            identity=identity,
            history_block=render_session_history_block(
                db, config, session_id=package.session_id, query=package.query
            ),
        )
    except Exception as error:
        return _retrieval_failed(db, package, "deep_retrieval_failed", "deep_retrieval", error, runtime_config)
    useful = deep_result["useful_chunks"]
    used_node_ids = [nid for nid in (node_identity(c) for c in useful) if nid]
    package.pre_built_answer = {
        "answer": deep_result["answer"],
        "used_node_ids": used_node_ids,
        "adapter": runtime_config.answer_adapter,
        "model": runtime_config.ollama_model,
    }
    package.prompt = {
        "prompt_text": "",
        "budget": {},
        "context_metadata": {
            "included": [{"node_id": nid} for nid in used_node_ids],
            "evidence_summary": {
                "included_node_count": len(used_node_ids),
                "source_documents": [],
            },
            "skipped": [],
        },
    }
    package.retrieval_status = "deep_context" if useful else "deep_no_context"
    package.deep_trace = list(deep_result.get("trace") or [])
    package.selected_node_id = package.focus_node_id
    for entry in package.deep_trace:
        if entry.get("step") == "sufficiency":
            package.process_trace.append(
                {
                    "step": "sufficiency",
                    "input": {},
                    "output": {
                        "context_sufficiency_score": entry.get("context_sufficiency_score"),
                        "recursion": entry.get("recursion"),
                        "remaining_uncertainty_count": len(entry.get("remaining_uncertainty") or []),
                    },
                }
            )
    package.process_trace.append(
        {
            "step": "deep_retrieval",
            "input": {"query": package.query, "session_id": package.session_id, "mode": "deep"},
            "output": {
                "ok": True,
                "useful_count": len(useful),
                "rounds": deep_result["rounds"],
                "trace": deep_result["trace"],
            },
        }
    )
    return {"ok": True, "package": package.to_dict(), "phase": "retrieval"}


def _synthesize_and_persist(
    db: Database,
    config: AppConfig,
    runtime_config: RuntimeConfig,
    package: AnswerRetrievalPackage,
) -> dict[str, Any]:
    prompt = package.prompt or {}
    adapter_step = {
        "step": "answer_adapter",
        "input": {
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "prompt_text": prompt.get("prompt_text"),
            "controller_decision": package.controller_decision,
            "timeout_seconds": runtime_config.ollama_timeout_seconds
            if runtime_config.answer_adapter.startswith("ollama")
            else None,
        },
        "output": {},
    }
    package.process_trace.append(adapter_step)
    try:
        answer = answer_adapter(runtime_config).answer(prompt)
    except Exception as error:
        finish_answer_process_run(
            db,
            package.process_run_id,
            status="blocked",
            current_step_id="answer_adapter_failed",
            exception=answer_exception_payload(
                "answer_adapter_failed",
                "Inspect adapter/model configuration and retry.",
                error,
            ),
        )
        adapter_step["output"] = {"ok": False, "error": str(error)}
        return attach_answer_activity(
            {
                "ok": False,
                "reason": "answer_adapter_failed",
                "message": str(error),
                "adapter": runtime_config.answer_adapter,
                "model": runtime_config.ollama_model,
                "focus_node_id": package.selected_node_id,
                "process_run_id": package.process_run_id,
                "process_trace": package.process_trace,
            }
        )
    adapter_step["output"] = {
        "ok": True,
        "answer": answer["answer"],
        "used_node_ids": answer["used_node_ids"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
    }
    return _persist_answer(db, config, runtime_config, package, answer)


def _persist_prebuilt_answer(
    db: Database,
    config: AppConfig,
    runtime_config: RuntimeConfig,
    package: AnswerRetrievalPackage,
) -> dict[str, Any]:
    answer = package.pre_built_answer or {}
    return _persist_answer(db, config, runtime_config, package, answer)


def _persist_answer(
    db: Database,
    config: AppConfig,
    runtime_config: RuntimeConfig,
    package: AnswerRetrievalPackage,
    answer: dict[str, Any],
) -> dict[str, Any]:
    prompt = package.prompt or {}
    try:
        exchange_id = save_exchange(
            db,
            query=package.query,
            answer=answer,
            prompt=prompt,
            focus_node_id=package.selected_node_id,
            session_id=package.session_id,
            process_trace=package.process_trace,
            project_domain_id=package.project_domain_id,
            conversation_domain_id=package.conversation_domain_id,
        )
        schedule_turn_embedding(
            db, config, runtime_config, exchange_id, package.session_id, package.query, answer["answer"]
        )
        schedule_chunking(
            db, config, runtime_config, exchange_id, package.session_id, package.query, answer["answer"]
        )
    except Exception as error:
        finish_answer_process_run(
            db,
            package.process_run_id,
            status="blocked",
            current_step_id="answer_save_failed",
            exception=answer_exception_payload(
                "answer_save_failed",
                "Inspect exchange persistence and retry.",
                error,
            ),
        )
        package.process_trace.append(
            {
                "step": "save_exchange",
                "input": {"session_id": package.session_id, "focus_node_id": package.selected_node_id},
                "output": {"ok": False, "error": str(error), "type": type(error).__name__},
            }
        )
        return attach_answer_activity(
            {
                "ok": False,
                "reason": "answer_save_failed",
                "message": str(error),
                "adapter": runtime_config.answer_adapter,
                "model": runtime_config.ollama_model,
                "focus_node_id": package.selected_node_id,
                "process_run_id": package.process_run_id,
                "process_trace": package.process_trace,
            }
        )
    completed_step = "deep_retrieval" if package.pre_built_answer else "answer_adapter"
    finish_answer_process_run(
        db,
        package.process_run_id,
        status="completed",
        current_step_id="answer_saved",
        completed_step_id=completed_step,
        exchange_id=exchange_id,
    )
    result = {
        "ok": True,
        "exchange_id": exchange_id,
        "session_id": package.session_id,
        "project_domain_id": clean_domain_id(package.project_domain_id),
        "conversation_domain_id": clean_domain_id(
            package.conversation_domain_id,
            fallback=conversation_domain_id_for_session(package.session_id),
        ),
        "focus_node_id": package.selected_node_id,
        "query": package.query,
        "answer": answer["answer"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
        "used_node_ids": answer["used_node_ids"],
        "budget": prompt.get("budget", {}),
        "semantic": prompt.get("semantic", []),
        "semantic_summary": prompt.get("semantic_summary", ""),
        "retrieval_status": package.retrieval_status,
        "controller_decision": package.controller_decision,
        "process_run_id": package.process_run_id,
        "process_trace": package.process_trace,
        "phase": "synthesis",
    }
    return attach_answer_activity(result)


def _retrieval_failed(
    db: Database,
    package: AnswerRetrievalPackage,
    reason: str,
    step_name: str,
    error: Exception,
    runtime_config: RuntimeConfig,
) -> dict[str, Any]:
    finish_answer_process_run(
        db,
        package.process_run_id,
        status="blocked",
        current_step_id=f"{step_name}_failed",
        exception=answer_exception_payload(reason, "Inspect retrieval and retry.", error),
    )
    package.process_trace.append(
        {
            "step": step_name,
            "input": {"query": package.query, "session_id": package.session_id},
            "output": {"ok": False, "error": str(error)},
        }
    )
    return {
        "ok": False,
        "reason": reason,
        "message": str(error),
        "adapter": runtime_config.answer_adapter,
        "model": runtime_config.ollama_model,
        "focus_node_id": package.selected_node_id,
        "process_run_id": package.process_run_id,
        "process_trace": package.process_trace,
        "phase": "retrieval",
    }
