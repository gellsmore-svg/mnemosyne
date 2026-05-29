from __future__ import annotations

from typing import Any


def answer_activity_report(result: dict[str, Any]) -> dict[str, Any]:
    trace = result.get("process_trace") or []
    return {
        "schema_version": 1,
        "kind": "answer_activity_report",
        "status": "completed" if result.get("ok") else "blocked",
        "query_understanding": query_understanding_section(result, trace),
        "context_construction": context_construction_section(trace),
        "response_generation": response_generation_section(result, trace),
        "llm_activity": llm_activity_section(trace),
        "system_functions": system_functions_section(trace),
    }


def query_understanding_section(
    result: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_step = first_step(trace, "user_prompt") or {}
    prompt_input = prompt_step.get("input") or {}
    return {
        "interpreted_question": result.get("query") or prompt_input.get("query"),
        "session_id": result.get("session_id") or prompt_input.get("session_id"),
        "focus_node_id": result.get("focus_node_id") or prompt_input.get("focus_node_id"),
        "retrieval_mode": prompt_input.get("retrieval_mode"),
    }


def context_construction_section(trace: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval_step = first_step(trace, "retrieval_context")
    if retrieval_step:
        output = retrieval_step.get("output") or {}
        metadata = output.get("context_metadata") or {}
        return {
            "status": output.get("retrieval_status") or metadata.get("retrieval_status"),
            "focus_node_id": output.get("focus_node_id"),
            "included_nodes": compact_included_nodes(metadata.get("included") or []),
            "skipped_count": len(metadata.get("skipped") or []),
            "source_fallback": metadata.get("source_fallback"),
            "trust_diagnostic": output.get("trust_diagnostic"),
        }
    iterations = [step for step in trace if step.get("step") == "memory_agent_iteration"]
    return {
        "status": "agentic",
        "iterations": len(iterations),
        "tool_calls": sum(len(((step.get("output") or {}).get("tool_results") or [])) for step in iterations),
        "stop_reasons": [
            (step.get("output") or {}).get("stop_reason")
            for step in iterations
            if (step.get("output") or {}).get("stop_reason")
        ],
    }


def response_generation_section(
    result: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    adapter_step = first_step(trace, "answer_adapter") or {}
    adapter_input = adapter_step.get("input") or {}
    adapter_output = adapter_step.get("output") or {}
    return {
        "adapter": result.get("adapter") or adapter_input.get("adapter"),
        "model": result.get("model") or adapter_input.get("model"),
        "ok": bool(adapter_output.get("ok", result.get("ok"))),
        "used_node_ids": result.get("used_node_ids") or adapter_output.get("used_node_ids") or [],
        "answer_chars": len(result.get("answer") or adapter_output.get("answer") or ""),
    }


def llm_activity_section(trace: list[dict[str, Any]]) -> dict[str, Any]:
    calls = []
    for step in trace:
        if step.get("step") not in {"memory_agent_iteration", "answer_adapter"}:
            continue
        step_input = step.get("input") or {}
        step_output = step.get("output") or {}
        calls.append(
            {
                "step": step.get("step"),
                "purpose": llm_call_purpose(step.get("step")),
                "adapter": step_input.get("adapter"),
                "model": step_input.get("model"),
                "ok": step_output.get("ok"),
                "format": step_input.get("format"),
            }
        )
    return {"call_count": len(calls), "calls": calls}


def system_functions_section(trace: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = set()
    functions = []
    for step in trace:
        name = step.get("step")
        if name in seen:
            continue
        seen.add(name)
        functions.append(
            {
                "function": human_step_name(name),
                "purpose": step_purpose(name),
            }
        )
    return functions


def first_step(trace: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((step for step in trace if step.get("step") == name), None)


def compact_included_nodes(included: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for item in included:
        compact.append(
            {
                "node_id": item.get("node_id"),
                "title": item.get("title"),
                "role": item.get("role"),
                "distance": item.get("distance"),
            }
        )
    return compact


def llm_call_purpose(step: str | None) -> str:
    if step == "memory_agent_iteration":
        return "Plan and request read-only memory tools for context gathering."
    if step == "answer_adapter":
        return "Generate the final response from the constructed context."
    return "LLM call."


def human_step_name(step: str | None) -> str:
    return {
        "user_prompt": "Capture User Prompt",
        "retrieval_context": "Construct Retrieval Context",
        "memory_agent_iteration": "Run Memory-Agent Planner",
        "answer_adapter": "Generate Final Answer",
        "save_exchange": "Persist Exchange",
    }.get(step or "", str(step or "Unknown Step"))


def step_purpose(step: str | None) -> str:
    return {
        "user_prompt": "Records the submitted question, session, focus node, and requested runtime settings.",
        "retrieval_context": "Selects and packages source evidence for the answer model.",
        "memory_agent_iteration": "Lets the memory-agent decide which read-only retrieval tools to call.",
        "answer_adapter": "Calls the configured answer model or mock adapter.",
        "save_exchange": "Stores the answer, prompt envelope, and process trace for later review.",
    }.get(step or "", "Records a system action in the answer flow.")
