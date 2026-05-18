from __future__ import annotations

import json
import re
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
    search_nodes,
)
from mnemosyne.sessions.exchanges import save_exchange


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

    selected_node_id = focus_node_id or select_focus_node(db, query)
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
    planner_prompt = build_planner_prompt(query=query, focus_node_id=focus_node_id)
    planner_envelope = {
        "prompt_text": planner_prompt,
        "context_text": "",
        "context_metadata": {"included": []},
    }
    planner_step = {
        "step": "planner_adapter",
        "input": {
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "prompt_text": planner_prompt,
            "allowed_tools": allowed_tool_specs(),
        },
        "output": {},
    }
    process_trace.append(planner_step)
    raw_planner_answer = None
    try:
        planner_answer = answer_adapter(runtime_config).answer(planner_envelope)
        raw_planner_answer = planner_answer["answer"]
        tool_calls = parse_tool_calls(planner_answer["answer"])
        planner_step["output"] = {
            "ok": True,
            "raw_answer": raw_planner_answer,
            "tool_calls": tool_calls,
        }
    except Exception as error:
        tool_calls = [{"tool": "search_nodes", "arguments": {"query": query, "limit": 5}}]
        planner_step["output"] = {
            "ok": False,
            "raw_answer": raw_planner_answer,
            "error": str(error),
            "fallback_tool_calls": tool_calls,
        }

    tool_step = {
        "step": "tool_execution",
        "input": {
            "tool_calls": tool_calls,
        },
        "output": {},
    }
    process_trace.append(tool_step)
    tool_results = execute_tool_calls(db, tool_calls, original_query=query)
    tool_step["output"] = {
        "tool_results": tool_results,
    }

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


def ranked_focus_matches(
    db: Database,
    query: str,
    label: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    cleaned_query = normalize_query_text(query)
    matches = search_nodes(db, query=cleaned_query, label=label, limit=limit)
    if cleaned_query:
        seen = {row["node_id"] for row in matches}
        for fallback_query in fallback_queries(cleaned_query):
            fallback_results = search_nodes(
                db,
                query=fallback_query,
                label=label,
                limit=fallback_candidate_limit(limit),
            )
            for row in fallback_results:
                if row["node_id"] not in seen:
                    seen.add(row["node_id"])
                    matches.append(row)
    matches.sort(key=lambda row: score_node_match(row, cleaned_query), reverse=True)
    return matches[:limit]


def build_planner_prompt(query: str, focus_node_id: str | None = None) -> str:
    return "\n".join(
        [
            "You are the Mnemosyne retrieval planner.",
            "Choose which Mnemosyne tools to call before the answer model replies.",
            "Return only valid JSON with this shape:",
            '{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"...","limit":5}}]}',
            "Allowed tools:",
            json.dumps(allowed_tool_specs(), indent=2),
            "",
            "Rules:",
            "- Use search_nodes for ordinary text queries.",
            "- Preserve the user's substantive intent terms in search_nodes queries.",
            "- Use compile_context when a focus_node_id is provided or a specific node id is known.",
            "- Use list_documents only when the user asks what documents are available.",
            "- Use at most 3 tool calls.",
            "- Do not answer the user.",
            "",
            f"focus_node_id: {focus_node_id or 'none'}",
            "",
            "User prompt:",
            query,
        ]
    )


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
        return stripped
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        raise ValueError("Planner did not return a JSON object.")
    return match.group(0)


def normalize_tool_call(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        raise ValueError("Each tool call must be an object.")
    tool = call.get("tool")
    if tool not in {"search_nodes", "compile_context", "list_documents"}:
        raise ValueError(f"Unsupported planner tool: {tool}")
    arguments = call.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool call arguments must be an object.")
    return {"tool": tool, "arguments": arguments}


def execute_tool_calls(
    db: Database,
    tool_calls: list[dict[str, Any]],
    original_query: str | None = None,
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
    matches = search_nodes(db, query=cleaned_query, label=label, limit=limit)
    details: dict[str, Any] = {
        "normalized_query": cleaned_query,
        "ranking_query": ranking_query,
        "fallback_queries": [],
    }
    if not matches and ranking_query:
        seen = {row["node_id"] for row in matches}
        for fallback_query in fallback_queries(ranking_query):
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
    matches.sort(key=lambda row: score_node_match(row, ranking_query), reverse=True)
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
    for term in re.findall(r"[A-Za-z0-9]+", " ".join(parts)):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return " ".join(terms)


def fallback_queries(query: str) -> list[str]:
    terms = []
    for term in re.split(r"[^A-Za-z0-9]+", query):
        if len(term) >= 4 and term.lower() not in {
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
        }:
            terms.append(term)
    deduped = []
    for term in sorted(terms, key=len, reverse=True):
        if term.lower() not in {seen.lower() for seen in deduped}:
            deduped.append(term)
    return deduped[:5]


def score_node_match(row: dict[str, Any], query: str | None) -> int:
    if not query:
        return 0
    terms = [
        term.lower()
        for term in re.split(r"[^A-Za-z0-9]+", query)
        if len(term) >= 4
    ]
    title = (row.get("title") or "").lower()
    text = (row.get("text_preview") or "").lower()
    score = 0
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
    if query.lower() in title:
        score += 20
    if query.lower() in text:
        score += 8
    if row.get("labels") and "source_section" in row.get("labels", []):
        score += 3
    if row.get("labels") and "source_root" in row.get("labels", []):
        score -= 20
    if text.strip() in {"", "---"}:
        score -= 8
    return score


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
            "estimated_overhead_tokens": 0,
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
            "char_budget": len(context_text),
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
                    "top_context": contexts[0] if contexts else None,
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
            top_context = output.get("top_context")
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
            if top_context:
                rendered = render_context_document(top_context, char_budget=4000)
                lines.extend(["", rendered["text"].strip()])
            blocks.append("\n".join(lines))
            continue
        blocks.append(json.dumps(result, indent=2, default=str))
    return "\n\n".join(blocks)


def included_nodes_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    included: dict[str, dict[str, Any]] = {}
    for result in tool_results:
        if not result.get("ok"):
            continue
        collect_included_nodes(result.get("output"), included)
    return list(included.values())


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
