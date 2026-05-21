from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from pymongo.database import Database

from mnemosyne.adapters.answer import answer_adapter
from mnemosyne.config import AppConfig
from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    build_prompt_envelope_without_context,
    compile_context,
    list_documents,
    render_context_document,
    render_record,
    search_nodes,
)
from mnemosyne.sessions.active_documents import list_active_documents
from mnemosyne.sessions.exchanges import save_exchange


TERMINAL_FALLBACK_REASONS = {"memory_agent_decision_failed"}
ANSWER_CONTEXT_CHAR_BUDGET = 4000
NEAR_MATCH_MIN_SCORE = 0.78
NEAR_MATCH_MAX_VOCABULARY = 2000
QUERY_STOPWORDS = {
    "what",
    "does",
    "with",
    "from",
    "that",
    "this",
    "when",
    "where",
    "which",
    "says",
    "for",
    "the",
    "and",
    "into",
    "about",
    "find",
    "list",
    "show",
    "tell",
    "give",
    "help",
    "compare",
    "note",
    "your",
    "will",
}


def answer_query(
    db: Database,
    config: AppConfig,
    query: str,
    focus_node_id: str | None = None,
    session_id: str = "default",
    answer_adapter_name: str | None = None,
    ollama_model: str | None = None,
    retrieval_mode: str | None = None,
) -> dict[str, Any]:
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
            "output": {
                "submitted_prompt": query,
            },
        }
    ]
    runtime_config = config.runtime.model_copy()
    if answer_adapter_name:
        runtime_config.answer_adapter = answer_adapter_name
    if ollama_model:
        runtime_config.ollama_model = ollama_model
    if retrieval_mode:
        runtime_config.retrieval_mode = retrieval_mode
    if runtime_config.retrieval_mode == "agentic":
        return answer_query_agentic(
            db=db,
            config=config,
            runtime_config=runtime_config,
            query=query,
            focus_node_id=focus_node_id,
            selected_node_id=focus_node_id,
            session_id=session_id,
            process_trace=process_trace,
        )

    selected_node_id = focus_node_id
    selected_node_source = "provided" if focus_node_id else None
    if not selected_node_id and active_document_reference_query(query):
        selected_node_id = select_active_document_focus_node(db, query, session_id)
        if selected_node_id:
            selected_node_source = "active_document"
    if not selected_node_id:
        selected_node_id = select_focus_node(db, query)
        if selected_node_id:
            selected_node_source = "corpus"
    retrieval_status = "matched_context"
    if selected_node_id:
        context = compile_context(db, selected_node_id)
        if context:
            prompt = build_prompt_envelope(
                context,
                query=query,
                token_budget=config.retrieval.prompt_token_budget,
                reserved_response_tokens=config.retrieval.reserved_response_tokens,
            )
        else:
            retrieval_status = "missing_context"
            prompt = build_prompt_envelope_without_context(
                query=query,
                token_budget=config.retrieval.prompt_token_budget,
                reserved_response_tokens=config.retrieval.reserved_response_tokens,
            )
            prompt["context_metadata"]["retrieval_status"] = retrieval_status
    else:
        retrieval_status = "no_focus_node"
        prompt = build_prompt_envelope_without_context(
            query=query,
            token_budget=config.retrieval.prompt_token_budget,
            reserved_response_tokens=config.retrieval.reserved_response_tokens,
        )
    process_trace.append(
        {
            "step": "retrieval_context",
            "input": {
                "query": query,
                "provided_focus_node_id": focus_node_id,
                "selected_node_id": selected_node_id,
                "selected_node_source": selected_node_source,
                "mode": config.runtime.retrieval_mode,
            },
            "output": {
                "retrieval_status": retrieval_status,
                "focus_node_id": selected_node_id,
                "context_text": prompt["context_text"],
                "context_metadata": prompt["context_metadata"],
                "budget": prompt["budget"],
            },
        }
    )
    adapter_step = {
        "step": "answer_adapter",
        "input": {
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "prompt_text": prompt["prompt_text"],
            "timeout_seconds": runtime_config.ollama_timeout_seconds
            if runtime_config.answer_adapter.startswith("ollama")
            else None,
        },
        "output": {},
    }
    process_trace.append(adapter_step)
    try:
        answer = answer_adapter(runtime_config).answer(prompt)
    except Exception as error:
        adapter_step["output"] = {
            "ok": False,
            "error": str(error),
        }
        return {
            "ok": False,
            "reason": "answer_adapter_failed",
            "message": str(error),
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "focus_node_id": selected_node_id,
            "process_trace": process_trace,
        }
    adapter_step["output"] = {
        "ok": True,
        "answer": answer["answer"],
        "used_node_ids": answer["used_node_ids"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
    }
    exchange_id = save_exchange(
        db,
        query=query,
        answer=answer,
        prompt=prompt,
        focus_node_id=selected_node_id,
        session_id=session_id,
        process_trace=process_trace,
    )
    return {
        "ok": True,
        "exchange_id": exchange_id,
        "session_id": session_id,
        "focus_node_id": selected_node_id,
        "query": query,
        "answer": answer["answer"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
        "used_node_ids": answer["used_node_ids"],
        "budget": prompt["budget"],
        "retrieval_status": retrieval_status,
        "process_trace": process_trace,
    }


def answer_query_agentic(
    db: Database,
    config: AppConfig,
    runtime_config,
    query: str,
    focus_node_id: str | None,
    selected_node_id: str | None,
    session_id: str,
    process_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    tool_results = run_memory_agent_loop(
        db=db,
        runtime_config=runtime_config,
        query=query,
        focus_node_id=focus_node_id,
        session_id=session_id,
        max_iterations=config.retrieval.memory_agent_max_iterations,
        process_trace=process_trace,
    )

    prompt = build_agentic_answer_envelope(
        query=query,
        tool_results=tool_results,
        token_budget=config.retrieval.prompt_token_budget,
        reserved_response_tokens=config.retrieval.reserved_response_tokens,
    )
    retrieval_status = "agentic_tool_context" if prompt["context_metadata"]["included"] else "agentic_no_tool_context"
    adapter_step = {
        "step": "answer_adapter",
        "input": {
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "prompt_text": prompt["prompt_text"],
            "timeout_seconds": runtime_config.ollama_timeout_seconds
            if runtime_config.answer_adapter.startswith("ollama")
            else None,
        },
        "output": {},
    }
    process_trace.append(adapter_step)
    try:
        answer = answer_adapter(runtime_config).answer(prompt)
    except Exception as error:
        adapter_step["output"] = {
            "ok": False,
            "error": str(error),
        }
        return {
            "ok": False,
            "reason": "answer_adapter_failed",
            "message": str(error),
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "focus_node_id": selected_node_id,
            "process_trace": process_trace,
        }
    adapter_step["output"] = {
        "ok": True,
        "answer": answer["answer"],
        "used_node_ids": answer["used_node_ids"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
    }
    exchange_id = save_exchange(
        db,
        query=query,
        answer=answer,
        prompt=prompt,
        focus_node_id=selected_node_id,
        session_id=session_id,
        process_trace=process_trace,
    )
    return {
        "ok": True,
        "exchange_id": exchange_id,
        "session_id": session_id,
        "focus_node_id": selected_node_id,
        "query": query,
        "answer": answer["answer"],
        "adapter": answer["adapter"],
        "model": answer.get("model"),
        "used_node_ids": answer["used_node_ids"],
        "budget": prompt["budget"],
        "retrieval_status": retrieval_status,
        "process_trace": process_trace,
    }


def select_focus_node(db: Database, query: str) -> str | None:
    matches = ranked_focus_matches(db, query, label="source_chunk", limit=5)
    if not matches:
        matches = ranked_focus_matches(db, query, label=None, limit=5)
    if not matches:
        return None
    return matches[0]["node_id"]


def select_active_document_focus_node(db: Database, query: str, session_id: str) -> str | None:
    active_documents = list_active_documents(db, session_id=session_id, limit=5)
    for active_document in active_documents:
        document_id = active_document.get("document_id")
        if not document_id:
            continue
        matches = ranked_focus_matches(
            db,
            query,
            label="source_chunk",
            limit=5,
            document_id=document_id,
        )
        if not matches:
            matches = ranked_focus_matches(
                db,
                query,
                label=None,
                limit=5,
                document_id=document_id,
            )
        if matches:
            return matches[0]["node_id"]
    return None


def ranked_focus_matches(
    db: Database,
    query: str,
    label: str | None,
    limit: int,
    document_id: str | None = None,
) -> list[dict[str, Any]]:
    cleaned_query = normalize_query_text(query)
    assembly = build_query_assembly(cleaned_query)
    matches = search_nodes(
        db,
        query=cleaned_query,
        label=label,
        document_id=document_id,
        limit=limit,
    )
    if cleaned_query:
        seen = {row["node_id"] for row in matches}
        for fallback_query in fallback_queries(assembly):
            fallback_results = search_nodes(
                db,
                query=fallback_query,
                label=label,
                document_id=document_id,
                limit=fallback_candidate_limit(limit),
            )
            for row in fallback_results:
                if row["node_id"] not in seen:
                    seen.add(row["node_id"])
                    matches.append(row)
        if not matches:
            assembly = build_query_assembly(
                cleaned_query,
                vocabulary=near_match_vocabulary(db),
            )
            for fallback_query in fallback_queries(assembly):
                fallback_results = search_nodes(
                    db,
                    query=fallback_query,
                    label=label,
                    document_id=document_id,
                    limit=fallback_candidate_limit(limit),
                )
                for row in fallback_results:
                    if row["node_id"] not in seen:
                        seen.add(row["node_id"])
                        matches.append(row)
    matches.sort(key=lambda row: score_node_match(row, assembly), reverse=True)
    return matches[:limit]


def active_document_reference_query(query: str) -> bool:
    normalized_query = normalize_query_text(query)
    if not normalized_query:
        return False
    normalized = normalized_query.lower()
    references = [
        "this document",
        "this source",
        "this file",
        "current document",
        "current source",
        "current file",
        "active document",
        "active source",
        "previous document",
        "previous source",
        "last document",
        "last source",
        "that document",
        "that source",
    ]
    return any(re.search(rf"\b{re.escape(reference)}\b", normalized) for reference in references)


def build_planner_prompt(query: str, focus_node_id: str | None = None) -> str:
    return build_memory_agent_prompt(
        query=query,
        focus_node_id=focus_node_id,
        session_id="default",
        active_documents=[],
        history=[],
    )


def build_memory_agent_prompt(
    query: str,
    focus_node_id: str | None,
    session_id: str,
    active_documents: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    query_assembly = build_query_assembly(query)
    return "\n".join(
        [
            "You are the Mnemosyne memory-agent.",
            "Your job is to gather memory/context for a separate final thinking LLM.",
            "Do not answer the user.",
            "Inspect prior tool results, decide whether more Mongo context is needed, and either call tools or stop.",
            "Return only valid JSON in one of these shapes:",
            '{"status":"continue","tool_calls":[{"tool":"search_nodes","arguments":{"query":"...","limit":5}}]}',
            '{"status":"done","tool_calls":[],"compiled_context_notes":"why the gathered context is sufficient or limited"}',
            "Allowed tools:",
            json.dumps(allowed_tool_specs(), indent=2),
            "",
            "Rules:",
            "- Use search_nodes for ordinary text queries.",
            "- Preserve the user's substantive intent terms in search_nodes queries.",
            "- Use compile_context when a focus_node_id is provided or a specific node id is known.",
            "- Use list_active_documents when session context may help resolve references such as this document, the previous source, or active project material.",
            "- Use list_documents only when the user asks what documents are available.",
            "- Use at most 3 tool calls per iteration.",
            "- Stop only when context is sufficient, clearly insufficient, or no further read-only tool call is useful.",
            "",
            f"focus_node_id: {focus_node_id or 'none'}",
            f"session_id: {session_id}",
            "",
            "Active documents:",
            json.dumps(active_documents, indent=2, default=str),
            "",
            "Query assembly:",
            render_query_assembly_guidance(query_assembly),
            "",
            "Prior memory-agent iterations:",
            json.dumps(history, indent=2, default=str),
            "",
            "User prompt:",
            query,
        ]
    )


def run_memory_agent_loop(
    db: Database,
    runtime_config,
    query: str,
    focus_node_id: str | None,
    session_id: str,
    max_iterations: int,
    process_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    memory_runtime = memory_agent_runtime_config(runtime_config)
    history: list[dict[str, Any]] = []
    all_tool_results: list[dict[str, Any]] = []
    for iteration in range(1, max(1, max_iterations) + 1):
        memory_prompt = build_memory_agent_prompt(
            query=query,
            focus_node_id=focus_node_id,
            session_id=session_id,
            active_documents=list_active_documents(db, session_id=session_id, limit=5),
            history=history,
        )
        step = {
            "step": "memory_agent_iteration",
            "input": {
                "iteration": iteration,
                "adapter": memory_runtime.answer_adapter,
                "model": memory_runtime.ollama_model,
                "prompt_text": memory_prompt,
                "allowed_tools": allowed_tool_specs(),
            },
            "output": {},
        }
        process_trace.append(step)
        raw_answer = None
        try:
            memory_answer = answer_adapter(memory_runtime).answer(
                {
                    "prompt_text": memory_prompt,
                    "context_text": "",
                    "context_metadata": {"included": []},
                }
            )
            raw_answer = memory_answer["answer"]
            decision = parse_memory_agent_decision(raw_answer)
        except Exception as error:
            decision = {
                "status": "continue",
                "tool_calls": [{"tool": "search_nodes", "arguments": {"query": query, "limit": 5}}],
                "error": str(error),
                "raw_answer": raw_answer,
                "fallback": True,
                "fallback_reason": "memory_agent_decision_failed",
            }

        tool_calls = decision.get("tool_calls", [])
        if decision.get("fallback") and all_tool_results:
            step["output"] = {
                "ok": False,
                "raw_answer": raw_answer,
                "decision": {
                    **decision,
                    "tool_calls": [],
                    "fallback_reason": "memory_agent_failed_after_tool_context",
                },
                "stopped": True,
                "stop_reason": "memory_agent_failed_after_tool_context",
            }
            break
        if not tool_calls and not all_tool_results:
            decision = {
                **decision,
                "status": "continue",
                "tool_calls": [{"tool": "search_nodes", "arguments": {"query": query, "limit": 5}}],
                "fallback": True,
                "fallback_reason": "memory_agent_returned_no_initial_tool_calls",
            }
            tool_calls = decision["tool_calls"]
        if decision.get("status") == "done" or not tool_calls:
            step["output"] = {
                "ok": True,
                "raw_answer": raw_answer,
                "decision": decision,
                "stopped": True,
            }
            break

        tool_results = execute_tool_calls(
            db,
            tool_calls,
            original_query=query,
            session_id=session_id,
        )
        all_tool_results.extend(tool_results)
        history.append(
            {
                "iteration": iteration,
                "decision": decision,
                "tool_results": summarize_tool_results_for_memory_agent(tool_results),
            }
        )
        step_ok = any(result.get("ok") for result in tool_results) if decision.get("fallback") else True
        step["output"] = {
            "ok": step_ok,
            "raw_answer": raw_answer,
            "decision": decision,
            "tool_results": tool_results,
        }
        if decision.get("fallback_reason") in TERMINAL_FALLBACK_REASONS:
            step["output"]["stopped"] = True
            step["output"]["stop_reason"] = (
                "fallback_context_gathered" if step_ok else "fallback_context_unavailable"
            )
            break
    return all_tool_results


def memory_agent_runtime_config(runtime_config):
    memory_runtime = runtime_config.model_copy()
    memory_runtime.answer_adapter = runtime_config.memory_agent_adapter or runtime_config.answer_adapter
    memory_runtime.ollama_model = runtime_config.memory_agent_model or runtime_config.ollama_model
    memory_runtime.ollama_format = runtime_config.memory_agent_ollama_format
    return memory_runtime


def parse_memory_agent_decision(text: str) -> dict[str, Any]:
    data = json.loads(extract_json_object(text), strict=False)
    calls = data.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("Memory-agent JSON must contain a tool_calls list.")
    status = data.get("status") or ("continue" if calls else "done")
    if status not in {"continue", "done"}:
        raise ValueError("Memory-agent status must be 'continue' or 'done'.")
    return {
        "status": status,
        "tool_calls": [normalize_tool_call(call) for call in calls[:3]],
        "compiled_context_notes": data.get("compiled_context_notes"),
    }


def summarize_tool_results_for_memory_agent(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for result in tool_results:
        output = result.get("output")
        item = {
            "tool": result.get("tool"),
            "arguments": result.get("arguments"),
            "ok": result.get("ok"),
        }
        if result.get("tool") == "search_nodes" and isinstance(output, dict):
            matches = output.get("matches") or []
            details = result.get("details") or {}
            query_assembly = details.get("query_assembly") or {}
            item["match_count"] = len(matches)
            item["top_matches"] = [
                {
                    "node_id": match.get("node_id"),
                    "title": match.get("title"),
                    "labels": match.get("labels"),
                    "text_preview": match.get("text_preview"),
                }
                for match in matches[:5]
            ]
            if query_assembly_has_values(query_assembly):
                item["query_assembly"] = {
                    "lexical_terms": query_assembly.get("lexical_terms") or [],
                    "exact_phrases": query_assembly.get("exact_phrases") or [],
                    "anchor_terms": query_assembly.get("anchor_terms") or [],
                    "near_match_terms": query_assembly.get("near_match_terms") or [],
                }
            fallback_queries = compact_fallback_query_details(
                details.get("fallback_queries") or []
            )
            if fallback_queries:
                item["fallback_queries"] = fallback_queries
        elif isinstance(output, dict):
            item["output_keys"] = sorted(output.keys())
            if output.get("focus_node_id"):
                item["focus_node_id"] = output.get("focus_node_id")
        elif isinstance(output, list):
            item["result_count"] = len(output)
        if result.get("error"):
            item["error"] = result.get("error")
        summary.append(item)
    return summary


def query_assembly_has_values(assembly: dict[str, Any]) -> bool:
    return any(
        assembly.get(key)
        for key in ["lexical_terms", "exact_phrases", "anchor_terms", "near_match_terms"]
    )


def compact_fallback_query_details(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for item in items:
        query = item.get("query")
        if not query:
            continue
        compact.append(
            {
                "query": query,
                "result_count": item.get("result_count", 0),
            }
        )
        if len(compact) >= 5:
            break
    return compact


def allowed_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "tool": "search_nodes",
            "arguments": {
                "query": "string",
                "label": "optional string",
                "limit": "optional integer, max 10",
            },
        },
        {
            "tool": "compile_context",
            "arguments": {
                "node_id": "string",
            },
        },
        {
            "tool": "list_active_documents",
            "arguments": {
                "limit": "optional integer, max 10",
            },
        },
        {
            "tool": "list_documents",
            "arguments": {
                "limit": "optional integer, max 10",
            },
        },
    ]


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    data = json.loads(extract_json_object(text), strict=False)
    calls = data.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("Planner JSON must contain a tool_calls list.")
    return [normalize_tool_call(call) for call in calls[:3]]


def extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            json.loads(stripped, strict=False)
            return stripped
        except json.JSONDecodeError:
            pass
    candidates = []
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"\{", stripped):
        try:
            parsed, end_index = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidate = stripped[match.start() : match.start() + end_index]
            if "tool_calls" in parsed or "status" in parsed:
                return candidate
            candidates.append(candidate)
    if candidates:
        return candidates[0]
    raise ValueError("Planner did not return a JSON object.")


def normalize_tool_call(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        raise ValueError("Each tool call must be an object.")
    tool = call.get("tool")
    if tool not in {"search_nodes", "compile_context", "list_active_documents", "list_documents"}:
        raise ValueError(f"Unsupported planner tool: {tool}")
    arguments = call.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool call arguments must be an object.")
    return {"tool": tool, "arguments": arguments}


def execute_tool_calls(
    db: Database,
    tool_calls: list[dict[str, Any]],
    original_query: str | None = None,
    session_id: str = "default",
) -> list[dict[str, Any]]:
    results = []
    for index, call in enumerate(tool_calls):
        tool = call["tool"]
        arguments = call["arguments"]
        try:
            details = {}
            if tool == "search_nodes":
                output, details = execute_search_nodes_tool(
                    db,
                    query=arguments.get("query"),
                    original_query=original_query,
                    label=arguments.get("label"),
                    limit=bounded_limit(arguments.get("limit"), default=5),
                )
            elif tool == "compile_context":
                node_id = arguments.get("node_id")
                if not node_id:
                    raise ValueError("compile_context requires node_id.")
                output = compile_context(db, node_id)
            elif tool == "list_documents":
                output = list_documents(db, limit=bounded_limit(arguments.get("limit"), default=5))
            elif tool == "list_active_documents":
                output = list_active_documents(
                    db,
                    session_id=session_id,
                    limit=bounded_limit(arguments.get("limit"), default=5),
                )
            else:
                raise ValueError(f"Unsupported tool: {tool}")
            results.append(
                {
                    "index": index,
                    "tool": tool,
                    "arguments": arguments,
                    "ok": True,
                    "output": output,
                    "details": details,
                }
            )
        except Exception as error:
            results.append(
                {
                    "index": index,
                    "tool": tool,
                    "arguments": arguments,
                    "ok": False,
                    "error": str(error),
                }
            )
    return results


def bounded_limit(value: Any, default: int = 5, maximum: int = 10) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, parsed))


def fallback_candidate_limit(result_limit: int) -> int:
    return max(result_limit * 4, 20)


def execute_search_nodes_tool(
    db: Database,
    query: str | None,
    original_query: str | None = None,
    label: str | None = None,
    limit: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cleaned_query = normalize_query_text(query)
    ranking_query = combined_query_text(cleaned_query, original_query)
    query_assembly = build_query_assembly(cleaned_query, original_query)
    matches = search_nodes(db, query=cleaned_query, label=label, limit=limit)
    details: dict[str, Any] = {
        "normalized_query": cleaned_query,
        "ranking_query": ranking_query,
        "query_assembly": query_assembly,
        "fallback_queries": [],
    }
    if not matches and ranking_query:
        query_assembly = build_query_assembly(
            cleaned_query,
            original_query,
            vocabulary=near_match_vocabulary(db),
        )
        details["query_assembly"] = query_assembly
        seen = {row["node_id"] for row in matches}
        for fallback_query in fallback_queries(query_assembly):
            fallback_results = search_nodes(
                db,
                query=fallback_query,
                label=label,
                limit=fallback_candidate_limit(limit),
            )
            details["fallback_queries"].append(
                {
                    "query": fallback_query,
                    "result_count": len(fallback_results),
                }
            )
            for row in fallback_results:
                if row["node_id"] not in seen:
                    seen.add(row["node_id"])
                    matches.append(row)
    matches.sort(key=lambda row: score_node_match(row, query_assembly), reverse=True)
    top_matches = matches[:limit]
    compiled_contexts = []
    for match in top_matches[:2]:
        context = compile_context(db, match["node_id"])
        if context:
            compiled_contexts.append(context)
    return {"matches": top_matches, "compiled_contexts": compiled_contexts}, details


def normalize_query_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def combined_query_text(query: str | None, original_query: str | None) -> str | None:
    parts = [part for part in [query, normalize_query_text(original_query)] if part]
    if not parts:
        return None
    terms = []
    seen = set()
    for term in re.findall(r"[A-Za-z0-9_]+", " ".join(parts)):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return " ".join(terms)


def build_query_assembly(
    query: str | None,
    original_query: str | None = None,
    vocabulary: list[str] | None = None,
) -> dict[str, Any]:
    ranking_query = combined_query_text(query, original_query)
    if not ranking_query:
        return {
            "ranking_query": None,
            "lexical_terms": [],
            "exact_phrases": [],
            "anchor_terms": [],
            "near_match_terms": [],
        }
    tokens = re.findall(r"[A-Za-z0-9_]+", ranking_query)
    lexical_terms = dedupe_preserve_order(
        token for token in tokens if is_query_content_term(token)
    )
    exact_phrases = dedupe_preserve_order(
        phrase
        for source in [query, original_query]
        for phrase in adjacent_content_phrases(source)
    )[:4]
    anchor_terms = dedupe_preserve_order(
        term
        for term in re.findall(r"\b[A-Z][A-Za-z0-9]{3,}\b", ranking_query)
        if term.lower() not in QUERY_STOPWORDS
    )
    return {
        "ranking_query": ranking_query,
        "lexical_terms": lexical_terms,
        "exact_phrases": exact_phrases,
        "anchor_terms": anchor_terms,
        "near_match_terms": near_match_terms(lexical_terms, vocabulary or []),
    }


def render_query_assembly_guidance(assembly: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Lexical terms: {format_list_for_prompt(assembly.get('lexical_terms'))}",
            f"- Exact phrases: {format_list_for_prompt(assembly.get('exact_phrases'))}",
            f"- Named anchors: {format_list_for_prompt(assembly.get('anchor_terms'))}",
            f"- Near-match terms: {format_near_matches_for_prompt(assembly.get('near_match_terms'))}",
            f"- Suggested fallback searches: {format_list_for_prompt(fallback_queries(assembly))}",
        ]
    )


def format_list_for_prompt(values: Any) -> str:
    if not values:
        return "none"
    return ", ".join(str(value) for value in values)


def format_near_matches_for_prompt(values: Any) -> str:
    if not values:
        return "none"
    formatted = []
    for item in values:
        if not isinstance(item, dict):
            continue
        source = item.get("source_term")
        candidate = item.get("candidate_term")
        score = item.get("score")
        if source and candidate:
            formatted.append(f"{source}->{candidate} ({score})")
    return ", ".join(formatted) if formatted else "none"


def is_query_content_term(term: str) -> bool:
    return len(term) >= 4 and term.lower() not in QUERY_STOPWORDS


def adjacent_content_phrases(value: str | None) -> list[str]:
    terms = [
        token
        for token in re.findall(r"[A-Za-z0-9_]+", value or "")
        if is_query_content_term(token)
    ]
    return [f"{left} {right}" for left, right in zip(terms, terms[1:])]


def dedupe_preserve_order(values) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        key = str(value).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(str(value))
    return deduped


def fallback_queries(query: str | dict[str, Any]) -> list[str]:
    assembly = query if isinstance(query, dict) else build_query_assembly(query)
    candidates = list(assembly.get("exact_phrases") or [])
    candidates.extend(
        item["candidate_term"]
        for item in assembly.get("near_match_terms") or []
        if isinstance(item, dict) and item.get("candidate_term")
    )
    candidates.extend(
        sorted(assembly.get("lexical_terms") or [], key=len, reverse=True)
    )
    return dedupe_preserve_order(candidates)[:8]


def score_node_match(row: dict[str, Any], query: str | dict[str, Any] | None) -> int:
    assembly = query if isinstance(query, dict) else build_query_assembly(query)
    ranking_query = assembly.get("ranking_query")
    if not ranking_query:
        return 0
    terms = [term.lower() for term in assembly.get("lexical_terms", [])]
    near_terms = [
        str(item.get("candidate_term")).lower()
        for item in assembly.get("near_match_terms", [])
        if isinstance(item, dict) and item.get("candidate_term")
    ]
    phrases = [phrase.lower() for phrase in assembly.get("exact_phrases", [])]
    title = (row.get("title") or "").lower()
    text = (row.get("text_preview") or "").lower()
    source_path = (
        (row.get("provenance") or {}).get("source_path")
        or (row.get("provenance") or {}).get("archive_path")
        or ""
    ).lower()
    searchable = " ".join([title, text, source_path])
    score = 0
    for phrase in phrases:
        if phrase in title:
            score += 14
        if phrase in text:
            score += 5
    for term in terms:
        if term in title:
            score += 5
        if term in text:
            score += 2
        if term in {"system", "purpose", "concept", "function", "role"}:
            if term in title:
                score += 10
            if term in text:
                score += 3
    for term in near_terms:
        if term in title:
            score += 3
        if term in text:
            score += 1
    anchor_terms = [term.lower() for term in assembly.get("anchor_terms", [])]
    for anchor in set(anchor_terms):
        if anchor in searchable:
            score += 18
        else:
            score -= 12
    if ranking_query.lower() in title:
        score += 20
    if ranking_query.lower() in text:
        score += 8
    if row.get("labels") and "source_section" in row.get("labels", []):
        score += 3
    if row.get("labels") and "source_root" in row.get("labels", []):
        score -= 30
    if is_document_metadata_match(title, text):
        score -= 35
    if is_low_content_match(row, text):
        score -= 40
    return score


def near_match_vocabulary(db: Database, limit: int = NEAR_MATCH_MAX_VOCABULARY) -> list[str]:
    values = []
    if not hasattr(db, "documents"):
        return []
    if hasattr(db, "label_definitions"):
        for definition in db.label_definitions.find({}, {"key": 1, "description": 1}).limit(limit):
            values.append(definition.get("key"))
            values.append(definition.get("description"))
    if hasattr(db, "nodes"):
        values.extend(db.nodes.distinct("labels"))
    for document in db.documents.find({}, {"title": 1, "source.path": 1}).limit(limit):
        values.append(document.get("title"))
        source = document.get("source") or {}
        values.append(source.get("path"))
    return vocabulary_terms(values, limit=limit)


def vocabulary_terms(values: list[Any], limit: int = NEAR_MATCH_MAX_VOCABULARY) -> list[str]:
    terms = []
    seen = set()
    for value in values:
        text = str(value or "")
        for label_like in re.findall(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+", text):
            key = label_like.lower()
            if key in seen or not is_query_content_term(label_like):
                continue
            seen.add(key)
            terms.append(label_like)
            if len(terms) >= limit:
                return terms
        for term in re.findall(r"[A-Za-z0-9]+", text):
            key = term.lower()
            if key in seen or not is_query_content_term(term):
                continue
            seen.add(key)
            terms.append(term)
            if len(terms) >= limit:
                return terms
    return terms


def near_match_terms(
    source_terms: list[str],
    vocabulary: list[str],
    min_score: float = NEAR_MATCH_MIN_SCORE,
    limit: int = 8,
) -> list[dict[str, Any]]:
    matches = []
    seen = set()
    vocabulary_by_key = {
        term.lower(): term
        for term in vocabulary
        if is_query_content_term(term)
    }
    for source_term in source_terms:
        source_key = source_term.lower()
        if source_key in vocabulary_by_key:
            continue
        best_candidate = None
        best_score = 0.0
        for candidate_key, candidate_term in vocabulary_by_key.items():
            if abs(len(source_key) - len(candidate_key)) > 2:
                continue
            score = SequenceMatcher(None, source_key, candidate_key).ratio()
            if score > best_score:
                best_candidate = candidate_term
                best_score = score
        if not best_candidate or best_score < min_score:
            continue
        key = (source_key, best_candidate.lower())
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            {
                "source_term": source_term,
                "candidate_term": best_candidate,
                "score": round(best_score, 2),
                "reason": "near_token_match",
            }
        )
        if len(matches) >= limit:
            break
    return matches


def is_document_metadata_match(title: str, text: str) -> bool:
    metadata_markers = ["**version:**", "**status:**", "**date:**"]
    has_metadata_markers = sum(1 for marker in metadata_markers if marker in text) >= 2
    return has_metadata_markers and any(
        marker in title for marker in ["technical design document", "requirements document"]
    )


def is_low_content_match(row: dict[str, Any], text: str) -> bool:
    if text.strip() not in {"", "---"}:
        return False
    labels = row.get("labels") or []
    return "source_chunk" in labels or "source_section" in labels


def build_agentic_answer_envelope(
    query: str,
    tool_results: list[dict[str, Any]],
    token_budget: int,
    reserved_response_tokens: int,
) -> dict[str, Any]:
    instruction = (
        "Answer the user using the Mnemosyne tool results. "
        "Prefer retrieved source context over general knowledge. "
        "If the tool results are insufficient, say so plainly."
    )
    answer_tool_results = prepare_tool_results_for_answer(tool_results)
    context_text = render_tool_results(answer_tool_results)
    overhead_text = "\n".join(
        [
            instruction,
            "",
            "## User Query",
            query,
            "",
            "## Mnemosyne Tool Results",
            "",
        ]
    )
    prompt_text = "\n".join(
        [
            instruction,
            "",
            "## User Query",
            query,
            "",
            "## Mnemosyne Tool Results",
            context_text,
        ]
    ).rstrip() + "\n"
    return {
        "system_instruction": instruction,
        "query": query,
        "context_text": context_text,
        "prompt_text": prompt_text,
        "budget": {
            "token_budget": token_budget,
            "reserved_response_tokens": reserved_response_tokens,
            "estimated_overhead_tokens": len(overhead_text) // 4 + 1,
            "available_context_tokens": max(0, token_budget - reserved_response_tokens),
            "estimated_prompt_tokens": len(prompt_text) // 4 + 1,
            "estimated_context_tokens": len(context_text) // 4 + 1,
            "estimated_total_with_reserved_response_tokens": len(prompt_text) // 4
            + 1
            + reserved_response_tokens,
        },
        "context_metadata": {
            "included": included_nodes_from_tool_results(answer_tool_results),
            "skipped": [],
            "used_chars": len(context_text),
            "char_budget": ANSWER_CONTEXT_CHAR_BUDGET,
            "retrieval_status": "agentic_tool_context",
            "tool_result_count": len(tool_results),
        },
    }


def prepare_tool_results_for_answer(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for result in tool_results:
        if result.get("tool") != "search_nodes" or not result.get("ok"):
            prepared.append(result)
            continue
        output = result.get("output") or {}
        if isinstance(output, list):
            matches = output
            contexts = []
        else:
            matches = output.get("matches") or []
            contexts = output.get("compiled_contexts") or []
        prepared.append(
            {
                **result,
                "output": {
                    "top_match": matches[0] if matches else None,
                    "top_contexts": assemble_search_contexts(
                        contexts[:2],
                        char_budget=ANSWER_CONTEXT_CHAR_BUDGET,
                    ),
                    "match_count": len(matches),
                },
            }
        )
    return prepared


def render_tool_results(tool_results: list[dict[str, Any]]) -> str:
    blocks = []
    for result in tool_results:
        if result.get("tool") == "search_nodes" and result.get("ok"):
            output = result.get("output") or {}
            top_match = output.get("top_match") or {}
            top_contexts = output.get("top_contexts") or []
            lines = [
                "### search_nodes",
                "",
                f"- Query: {result.get('arguments', {}).get('query') or '<none>'}",
                f"- Match count: {output.get('match_count', 0)}",
            ]
            if top_match:
                lines.extend(
                    [
                        f"- Top match: {top_match.get('title') or '<untitled>'}",
                        f"- Top node ID: {top_match.get('node_id')}",
                    ]
                )
            remaining_chars = ANSWER_CONTEXT_CHAR_BUDGET
            for index, context in enumerate(top_contexts, start=1):
                rendered = render_context_document(
                    context,
                    char_budget=remaining_chars,
                    heading_level=4,
                )
                text = rendered["text"].strip()
                if text:
                    lines.extend(["", f"Compiled context {index}:", "", text])
                    remaining_chars = max(0, remaining_chars - rendered["used_chars"])
                if remaining_chars <= 0:
                    break
            lines.extend(render_search_details_lines(result.get("details") or {}))
            blocks.append("\n".join(lines))
            continue
        blocks.append(json.dumps(result, indent=2, default=str))
    return "\n\n".join(blocks)


def render_search_details_lines(details: dict[str, Any]) -> list[str]:
    assembly = details.get("query_assembly") or {}
    lines = []
    if assembly:
        lines.append("")
        lines.extend(
            [
                "Search diagnostics:",
                f"- Lexical terms: {format_list_for_prompt(assembly.get('lexical_terms'))}",
                f"- Exact phrases: {format_list_for_prompt(assembly.get('exact_phrases'))}",
                f"- Named anchors: {format_list_for_prompt(assembly.get('anchor_terms'))}",
                f"- Near-match terms: {format_near_matches_for_prompt(assembly.get('near_match_terms'))}",
            ]
        )
    fallback_details = details.get("fallback_queries") or []
    if fallback_details:
        probes = []
        for item in fallback_details[:5]:
            query = item.get("query")
            if query:
                probes.append(f"{query} ({item.get('result_count', 0)})")
        if probes:
            lines.append(f"- Fallback searches: {format_list_for_prompt(probes)}")
    return lines


def assemble_search_contexts(
    contexts: list[dict[str, Any]],
    char_budget: int = 4000,
) -> list[dict[str, Any]]:
    assembled = []
    seen_nodes: set[str] = set()
    remaining = char_budget
    for context in contexts:
        records = []
        header_chars = len(
            render_context_document(
                {**context, "records": []},
                char_budget=remaining,
                heading_level=4,
            )["text"]
        )
        context_remaining = remaining - header_chars
        if context_remaining <= 0:
            break
        for record in context.get("records", []):
            node_id = str(record.get("node_id") or "")
            if not node_id or node_id in seen_nodes:
                continue
            block_chars = len(render_record(record))
            if block_chars > context_remaining:
                continue
            seen_nodes.add(node_id)
            records.append(record)
            context_remaining -= block_chars
        if records:
            remaining = context_remaining
            assembled.append({**context, "records": records})
        if remaining <= 0:
            break
    return assembled


def included_nodes_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    included: dict[str, dict[str, Any]] = {}
    for result in tool_results:
        if not result.get("ok"):
            continue
        if result.get("tool") == "search_nodes":
            collect_included_search_context_records(result.get("output"), included)
        else:
            collect_included_nodes(result.get("output"), included)
    return list(included.values())


def collect_included_search_context_records(
    output: Any,
    included: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(output, dict):
        return
    has_context_records = False
    for context in output.get("top_contexts") or []:
        for record in context.get("records", []):
            has_context_records = True
            node_id = str(record.get("node_id") or "")
            if not node_id or node_id in included:
                continue
            included[node_id] = {
                "node_id": node_id,
                "role": record.get("role") or "context_record",
                "distance": record.get("distance", 0),
                "chars": len(render_record(record)),
            }
    if not has_context_records:
        collect_included_search_top_match(output, included)


def collect_included_search_top_match(
    output: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> None:
    top_match = output.get("top_match")
    if not isinstance(top_match, dict):
        return
    node_id = str(top_match.get("node_id") or "")
    if not node_id or node_id in included:
        return
    title = str(top_match.get("title") or "")
    rendered_summary = f"- Top match: {title}\n- Top node ID: {node_id}"
    included[node_id] = {
        "node_id": node_id,
        "role": top_match.get("role") or "search_match",
        "distance": top_match.get("distance", 0),
        "chars": len(rendered_summary),
    }


def collect_included_nodes(value: Any, included: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        node_id = value.get("node_id") or value.get("focus_node_id")
        if node_id:
            included[str(node_id)] = {
                "node_id": str(node_id),
                "role": value.get("role") or "tool_result",
                "distance": value.get("distance", 0),
                "chars": len(json.dumps(value, default=str)),
            }
        for child in value.values():
            collect_included_nodes(child, included)
    elif isinstance(value, list):
        for item in value:
            collect_included_nodes(item, included)
