"""Tirzah's MCP tool handlers — the *memory in* seam for coding agents.

Keturah ships a generic stdio MCP server (`keturah.mcp.run_stdio_server`); this
module supplies the concrete Tirzah handlers it dispatches to, so an agent
(Codex CLI, Claude Code, …) can query the family's project memory as an MCP
tool. See `Noa/docs/codex-integration-plan.md` (the "memory in" seam).

Kept dependency-light and defensive: importing this never requires a live DB,
and a handler call with no reachable Mongo returns a structured ``{"error": …}``
rather than raising into the agent.
"""

from __future__ import annotations

from typing import Any, Callable

# Lazily-bootstrapped default DB (the MCP server is a standalone process spawned
# by the agent; it connects to the configured Mongo on first use).
_DEFAULT_DB: Any = None
_DEFAULT_DB_TRIED = False


def _default_db() -> Any:
    global _DEFAULT_DB, _DEFAULT_DB_TRIED
    if _DEFAULT_DB_TRIED:
        return _DEFAULT_DB
    _DEFAULT_DB_TRIED = True
    try:
        from tirzah.config import load_config
        from tirzah.db.client import get_database

        _DEFAULT_DB = get_database(load_config().mongo)
    except Exception:  # noqa: BLE001 - no reachable DB → handlers report cleanly
        _DEFAULT_DB = None
    return _DEFAULT_DB


_DEFAULT_CONFIG: Any = None
_DEFAULT_CONFIG_TRIED = False


def _default_config() -> Any:
    global _DEFAULT_CONFIG, _DEFAULT_CONFIG_TRIED
    if _DEFAULT_CONFIG_TRIED:
        return _DEFAULT_CONFIG
    _DEFAULT_CONFIG_TRIED = True
    try:
        from tirzah.config import load_config

        _DEFAULT_CONFIG = load_config()
    except Exception:  # noqa: BLE001
        _DEFAULT_CONFIG = None
    return _DEFAULT_CONFIG


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    """A small, agent-friendly projection of a memory node."""
    text = node.get("content") or node.get("text") or node.get("summary") or ""
    if isinstance(text, str) and len(text) > 400:
        text = text[:400] + "…"
    return {
        "node_id": str(node.get("node_id") or node.get("_id") or ""),
        "labels": list(node.get("labels") or []),
        "text": text,
        "document_id": str(node.get("document_id") or ""),
    }


def build_handlers(*, db: Any = None, client: Any = None) -> dict[str, Callable[..., Any]]:
    """Return the Tirzah MCP handlers, keyed by tool name (namespaced + short).

    ``db`` / ``client`` may be injected (tests / an already-configured process);
    otherwise the default DB and Milcah client are bootstrapped lazily on first
    call.
    """

    def search_memory(query: str = "", limit: int = 10, **_kw: Any) -> dict[str, Any]:
        """Search the family's graph memory for nodes matching a query."""
        if not str(query).strip():
            return {"error": "query is required"}
        store = db if db is not None else _default_db()
        if store is None:
            return {"error": "tirzah memory is unavailable (no reachable database)"}
        try:
            from tirzah.retrieval.queries import search_nodes

            nodes = search_nodes(store, query=query, limit=max(1, min(int(limit), 50)))
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent
            return {"error": f"search failed: {type(exc).__name__}: {exc}"}
        results = [_compact_node(n) for n in nodes]
        return {"query": query, "count": len(results), "results": results}

    def review(text: str = "", intent: str = "", **_kw: Any) -> dict[str, Any]:
        """Pressure-test a diff/design/claim for coherence via Milcah."""
        if not str(text).strip():
            return {"error": "text to review is required"}
        milcah = client
        if milcah is None:
            config = _default_config()
            try:
                from tirzah.coherence import make_client

                milcah = make_client(getattr(config, "runtime", config)) if config else None
            except Exception:  # noqa: BLE001
                milcah = None
        if milcah is None:
            return {"error": "milcah review is unavailable (not enabled/reachable)"}
        try:
            from tirzah.coherence import SpecialistRequest

            request = SpecialistRequest(
                query=f"Pressure-test the coherence of this work. Intent: {intent or 'n/a'}.",
                mode="coherence", context=str(text)[:6000], max_iterations=2,
            )
            result = milcah.run(request)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"review failed: {type(exc).__name__}: {exc}"}
        get = (lambda k, d=None: result.get(k, d)) if isinstance(result, dict) else (
            lambda k, d=None: getattr(result, k, d)
        )
        return {
            "objections": list(get("objections", []) or []),
            "claims": list(get("claims", []) or []),
            "evidence": list(get("evidence", []) or []),
            "confidence": float(get("confidence", 0.0) or 0.0),
            "terminal_reason": str(get("terminal_reason", "") or ""),
        }

    return {
        "tirzah.search_memory": search_memory,
        "search_memory": search_memory,
        "tirzah.review": review,
        "review": review,
    }
