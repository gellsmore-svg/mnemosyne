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
        "tool_repair_guidance": tool_repair_guidance_section(trace),
        "system_functions": system_functions_section(trace),
    }


def answer_activity_log(report: dict[str, Any]) -> str:
    query = report.get("query_understanding") or {}
    context = report.get("context_construction") or {}
    response = report.get("response_generation") or {}
    llm = report.get("llm_activity") or {}
    repairs = report.get("tool_repair_guidance") or []
    functions = report.get("system_functions") or []
    lines = [
        "Answer Activity Log",
        "",
        "What happened:",
        f"- Status: {report.get('status') or 'unknown'}.",
        f"- Question understood as: {query.get('interpreted_question') or 'not recorded'}.",
        f"- Session: {query.get('session_id') or 'not recorded'}.",
        "",
        "Context construction:",
        context_summary(context),
        "",
        "Response generation:",
        response_summary(response),
        "",
        "LLM activity:",
        llm_summary(llm),
        "",
        "Tool-call repair guidance:",
        tool_repair_summary(repairs),
        "",
        "System functions used:",
    ]
    if functions:
        for item in functions:
            lines.append(f"- {item.get('function')}: {item.get('purpose')}")
    else:
        lines.append("- No system functions were recorded.")
    return "\n".join(lines).strip() + "\n"


def context_summary(context: dict[str, Any]) -> str:
    status = context.get("status") or "unknown"
    if status == "agentic":
        lines = [
            f"- Agentic retrieval ran {context.get('iterations', 0)} planner iteration(s) "
            f"and executed {context.get('tool_calls', 0)} tool call(s)."
        ]
        decision = context.get("controller_decision") or {}
        if decision.get("reason"):
            lines.append(f"- Controller decision: {decision.get('reason')}")
        lines.extend(evidence_summary_lines(context.get("evidence_summary")))
        return "\n".join(lines)
    lines = [f"- Retrieval status: {status}."]
    focus_node_id = context.get("focus_node_id")
    if focus_node_id:
        lines.append(f"- Focus node selected: {focus_node_id}.")
    included = context.get("included_nodes") or []
    if included:
        lines.append(f"- Source nodes included: {len(included)}.")
        for node in included[:5]:
            lines.append(
                f"  - {node.get('title') or 'Untitled node'} ({node.get('role') or 'context'}, "
                f"distance {node.get('distance')})."
            )
    else:
        lines.append("- No repository nodes were included in the final context.")
        decision = context.get("controller_decision") or context.get("retrieval_decision") or {}
        if decision.get("reason"):
            owner = decision.get("current_owner")
            owner_text = f" ({owner})" if owner else ""
            lines.append(f"- Controller decision{owner_text}: {decision.get('reason')}")
    fallback = context.get("source_fallback")
    if fallback:
        title = fallback.get("title") or fallback.get("document_id") or "active document"
        lines.append(f"- Used a bounded source-file excerpt from {title}.")
    skipped_count = context.get("skipped_count") or 0
    if skipped_count:
        lines.append(f"- Skipped {skipped_count} candidate record(s) because of budget or filtering.")
    lines.extend(
        evidence_summary_lines(
            context.get("evidence_summary"),
            include_included_nodes=False,
            include_source_fallback=False,
            include_source_documents=False,
        )
    )
    return "\n".join(lines)


def evidence_summary_lines(
    summary: dict[str, Any] | None,
    *,
    include_included_nodes: bool = True,
    include_source_fallback: bool = True,
    include_source_documents: bool = True,
) -> list[str]:
    if not isinstance(summary, dict):
        return []
    lines = []
    included_count = summary.get("included_node_count")
    if include_included_nodes and included_count is not None:
        lines.append(f"- Evidence summary: {included_count} repository node(s) included.")
    if include_source_fallback and summary.get("source_fallback_used"):
        lines.append("- Evidence summary: source-file fallback was used.")
    tool_count = summary.get("tool_result_count")
    if tool_count is not None:
        successful = summary.get("successful_tool_count", 0)
        failed = summary.get("failed_tool_count", 0)
        lines.append(
            f"- Evidence summary: {tool_count} memory-tool result(s), "
            f"{successful} successful and {failed} failed."
        )
    vector_edges = summary.get("vector_edge_evidence_count")
    if vector_edges:
        lines.append(
            f"- Evidence summary: {vector_edges} vector-derived reviewed edge(s) influenced retrieved context."
        )
    documents = summary.get("source_documents") or []
    if include_source_documents and documents:
        titles = [
            document.get("title") or document.get("document_id") or "untitled source"
            for document in documents[:3]
        ]
        more = len(documents) - len(titles)
        suffix = f", plus {more} more" if more > 0 else ""
        lines.append(f"- Evidence sources: {', '.join(titles)}{suffix}.")
    return lines


def response_summary(response: dict[str, Any]) -> str:
    used = response.get("used_node_ids") or []
    ok_text = "succeeded" if response.get("ok") else "did not complete"
    return "\n".join(
        [
            f"- Final answer generation {ok_text}.",
            f"- Adapter/model: {response.get('adapter') or 'unknown'} / {response.get('model') or 'unknown'}.",
            f"- Repository nodes shown to the answer model: {len(used)}.",
            f"- Final answer length: {response.get('answer_chars', 0)} characters.",
        ]
    )


def llm_summary(llm: dict[str, Any]) -> str:
    calls = llm.get("calls") or []
    if not calls:
        return "- No LLM calls were recorded."
    lines = [f"- LLM calls made: {llm.get('call_count', len(calls))}."]
    for index, call in enumerate(calls, start=1):
        lines.append(
            f"  {index}. {call.get('purpose')} "
            f"Adapter/model: {call.get('adapter') or 'unknown'} / {call.get('model') or 'unknown'}."
        )
    return "\n".join(lines)


def tool_repair_summary(repairs: list[dict[str, Any]]) -> str:
    if not repairs:
        return "- No memory-tool repair guidance was needed."
    lines = [f"- Tirzah returned repair guidance for {len(repairs)} failed memory-tool call(s)."]
    for item in repairs[:5]:
        lines.append(
            f"  - {item.get('tool') or 'unknown tool'}: {item.get('error') or 'call failed'}"
        )
        if item.get("repair_instruction"):
            lines.append(f"    Repair: {item.get('repair_instruction')}")
    return "\n".join(lines)


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
            "controller_decision": output.get("controller_decision") or metadata.get("controller_decision"),
            "retrieval_decision": output.get("retrieval_decision"),
            "evidence_summary": metadata.get("evidence_summary"),
            "included_nodes": compact_included_nodes(metadata.get("included") or []),
            "skipped_count": len(metadata.get("skipped") or []),
            "source_fallback": metadata.get("source_fallback"),
            "trust_diagnostic": output.get("trust_diagnostic"),
        }
    iterations = [step for step in trace if step.get("step") == "memory_agent_iteration"]
    adapter_step = first_step(trace, "answer_adapter") or {}
    adapter_input = adapter_step.get("input") or {}
    return {
        "status": "agentic",
        "iterations": len(iterations),
        "controller_decision": adapter_input.get("controller_decision"),
        "evidence_summary": adapter_input.get("evidence_summary"),
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


def tool_repair_guidance_section(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repairs = []
    for step in trace:
        if step.get("step") != "memory_agent_iteration":
            continue
        step_input = step.get("input") or {}
        step_output = step.get("output") or {}
        for result in step_output.get("tool_results") or []:
            if result.get("ok") is not False:
                continue
            repairs.append(
                {
                    "iteration": step_input.get("iteration"),
                    "tool": result.get("tool"),
                    "arguments": result.get("arguments"),
                    "error": result.get("error"),
                    "usage": result.get("usage"),
                    "repair_instruction": result.get("repair_instruction"),
                }
            )
    return repairs


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
