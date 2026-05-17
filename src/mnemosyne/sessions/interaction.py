from __future__ import annotations

from typing import Any

from pymongo.database import Database

from mnemosyne.adapters.answer import answer_adapter
from mnemosyne.config import AppConfig
from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    build_prompt_envelope_without_context,
    compile_context,
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
) -> dict[str, Any]:
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
    runtime_config = config.runtime.model_copy()
    if answer_adapter_name:
        runtime_config.answer_adapter = answer_adapter_name
    if ollama_model:
        runtime_config.ollama_model = ollama_model
    try:
        answer = answer_adapter(runtime_config).answer(prompt)
    except Exception as error:
        return {
            "ok": False,
            "reason": "answer_adapter_failed",
            "message": str(error),
            "adapter": runtime_config.answer_adapter,
            "model": runtime_config.ollama_model,
            "focus_node_id": selected_node_id,
        }
    exchange_id = save_exchange(
        db,
        query=query,
        answer=answer,
        prompt=prompt,
        focus_node_id=selected_node_id,
        session_id=session_id,
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
    }


def select_focus_node(db: Database, query: str) -> str | None:
    matches = search_nodes(db, query=query, label="source_chunk", limit=1)
    if not matches:
        matches = search_nodes(db, query=query, limit=1)
    if not matches:
        return None
    return matches[0]["node_id"]
