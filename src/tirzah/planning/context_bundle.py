"""Accumulate granular interpretive tool results into a synthesis-ready bundle."""
from __future__ import annotations

from typing import Any


def ensure_bundle(artifacts: dict[str, Any]) -> dict[str, Any]:
    bundle = artifacts.setdefault("context_bundle", {"tool_results": []})
    bundle.setdefault("tool_results", [])
    return bundle


def append_tool_result(
    bundle: dict[str, Any],
    *,
    tool: str,
    output: Any,
    arguments: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    entry = {
        "index": len(bundle["tool_results"]),
        "tool": tool,
        "arguments": dict(arguments or {}),
        "ok": ok,
        "output": output,
        "details": dict(details or {}),
    }
    bundle["tool_results"].append(entry)
    return entry


_MATCH_TOOLS = (
    "search_nodes",
    "expand_proximity",
    "expand_graph_paths",
    "semantic_candidates",
)


def latest_search_matches(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return latest_tool_matches(bundle, ("search_nodes",))


def latest_tool_matches(bundle: dict[str, Any], tools: tuple[str, ...]) -> list[dict[str, Any]]:
    for result in reversed(bundle.get("tool_results") or []):
        if result.get("tool") not in tools or not result.get("ok"):
            continue
        output = result.get("output")
        if isinstance(output, dict):
            matches = output.get("matches")
            if isinstance(matches, list):
                return list(matches)
        if isinstance(output, list):
            return list(output)
    return []


def resolve_focus_node_id(bundle: dict[str, Any], answer_kwargs: dict[str, Any]) -> str | None:
    focus = answer_kwargs.get("focus_node_id") or answer_kwargs.get("node_id")
    if focus:
        return str(focus)
    for result in reversed(bundle.get("tool_results") or []):
        if result.get("tool") == "compile_context" and result.get("ok"):
            output = result.get("output") or {}
            if isinstance(output, dict) and output.get("focus_node_id"):
                return str(output["focus_node_id"])
    matches = latest_tool_matches(bundle, _MATCH_TOOLS)
    if matches:
        node_id = matches[0].get("node_id")
        return str(node_id) if node_id else None
    return None


def resolve_compile_node_id(bundle: dict[str, Any], answer_kwargs: dict[str, Any]) -> str | None:
    return resolve_focus_node_id(bundle, answer_kwargs)


def resolve_document_id(bundle: dict[str, Any], answer_kwargs: dict[str, Any]) -> str | None:
    document_id = answer_kwargs.get("document_id")
    if document_id:
        return str(document_id)
    for result in reversed(bundle.get("tool_results") or []):
        if not result.get("ok"):
            continue
        output = result.get("output") or {}
        if not isinstance(output, dict):
            continue
        document = output.get("document") or {}
        if isinstance(document, dict) and document.get("document_id"):
            return str(document["document_id"])
    return None


def compact_context_bundle_summary(bundle: dict[str, Any] | None) -> dict[str, Any]:
    tool_results = list((bundle or {}).get("tool_results") or [])
    return {
        "tool_count": len(tool_results),
        "tools": [str(row.get("tool")) for row in tool_results if row.get("tool")],
        "ok_count": sum(1 for row in tool_results if row.get("ok")),
    }


def resolve_web_fetch_url(bundle: dict[str, Any]) -> str | None:
    for result in reversed(bundle.get("tool_results") or []):
        if result.get("tool") != "web_search" or not result.get("ok"):
            continue
        output = result.get("output") or {}
        if not isinstance(output, dict):
            continue
        for source in output.get("sources") or []:
            if isinstance(source, dict) and source.get("url"):
                return str(source["url"])
    return None