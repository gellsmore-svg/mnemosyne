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
    output: dict[str, Any],
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


def latest_search_matches(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    for result in reversed(bundle.get("tool_results") or []):
        if result.get("tool") == "search_nodes" and result.get("ok"):
            output = result.get("output") or {}
            if isinstance(output, dict):
                return list(output.get("matches") or [])
    return []


def resolve_compile_node_id(bundle: dict[str, Any], answer_kwargs: dict[str, Any]) -> str | None:
    focus = answer_kwargs.get("focus_node_id") or answer_kwargs.get("node_id")
    if focus:
        return str(focus)
    matches = latest_search_matches(bundle)
    if matches:
        node_id = matches[0].get("node_id")
        return str(node_id) if node_id else None
    return None


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