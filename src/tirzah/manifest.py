"""Tirzah's Keturah manifest — the interfaces an LLM/orchestrator can consume.

Distinct from ``tirzah.capabilities`` (which introspects the resolved *adapter*
runtime): this is the catalogue of Tirzah's LLM-callable *interfaces*. It is built
from the seam contracts Tirzah already enforces (``tirzah.coherence`` for the
specialist call, ``tirzah.semantic`` for annotation) so the published manifest and
the validated contract never drift. Exposed at ``GET /api/capabilities`` (add
``?format=mcp`` for the Model Context Protocol ``tools/list`` view).
"""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from keturah import Manifest, Registry, capability, manifest

# Sibling products whose manifests Tirzah aggregates into the family registry.
_SIBLING_PACKAGES = ("milcah", "mahalath", "cairn", "hoglah")


def _tirzah_version() -> str:
    try:
        return _pkg_version("tirzah")
    except PackageNotFoundError:
        return "0.0.0+source"


def _milcah_coherence_capability():
    """The coherence_check capability from Milcah's OWN manifest, or None if absent.

    Federated discovery: when Milcah is installed it owns the canonical declaration;
    Tirzah advertises that rather than its local mirror. Lazy + fail-soft.
    """
    try:
        from milcah.manifest import build_manifest as _milcah_manifest

        for cap in _milcah_manifest().capabilities:
            if cap.name == "coherence_check":
                return cap
    except Exception:
        return None
    return None


def _specialist_capability():
    federated = _milcah_coherence_capability()
    if federated is not None:
        if "planner" not in federated.tags:
            federated.tags = [*federated.tags, "planner"]
        return federated

    # Local fallback: the mirror Tirzah keeps so the seam works without Milcah installed.
    from tirzah.coherence import REQUEST_FIELDS, SPECIALIST_MODES, TERMINAL_REASONS

    return capability(
        "coherence_check",
        "Dispatch a Milcah specialist call — coherence pressure-test or counter-framework "
        "research — for a claim/framework. Returns claims, objections, evidence, citations, a "
        "confidence in [0,1], and a terminal_reason. Invoked by the planner when a request "
        "warrants it; surfaces as a specialist.completed trace event.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the claim/framework to pressure-test"},
                "mode": {"type": "string", "enum": sorted(SPECIALIST_MODES), "default": "coherence"},
                "context": {"type": "string", "description": "the framework text to analyse"},
                "max_iterations": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 3,
                    "description": "upper bound on specialist recursion depth",
                },
                "trace_id": {"type": "string", "description": "caller trace identifier"},
                "session_id": {"type": "string", "description": "caller session identifier"},
            },
            "required": list(REQUEST_FIELDS),
        },
        output_schema={
            "type": "object",
            "properties": {
                "claims": {"type": "array", "items": {"type": "string"}},
                "objections": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "citations": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "terminal_reason": {"type": "string", "enum": sorted(TERMINAL_REASONS)},
                "trace_metadata": {"type": "object"},
            },
        },
        tags=["specialist", "milcah", "planner"],
    )


def _semantic_capability():
    from tirzah.semantic import SEMANTIC_LABEL_FIELDS

    return capability(
        "semantic_annotate",
        "Resolve terms to Mahalath MPL labels + senses for precise meaning. Returns labels with "
        "the term, mpl_label, canonical_term, senses, match_kind, is_stale.",
        input_schema={
            "type": "object",
            "properties": {"terms": {"type": "array", "items": {"type": "string"}}},
            "required": ["terms"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "object", "properties": {field: {} for field in SEMANTIC_LABEL_FIELDS}},
                }
            },
        },
        tags=["semantic", "mahalath"],
    )


# Planner-callable tools (tagged "planner") and the runtime flag that enables each.
# A tool with no entry here is always available.
_PLANNER_TOOL_ENABLED = {"coherence_check": "milcah_enabled"}


def planner_tools(runtime: object) -> list:
    """The manifest's planner-callable tool capabilities that are enabled right now."""
    enabled = []
    for cap in build_manifest().capabilities:
        if cap.kind != "tool" or "planner" not in cap.tags:
            continue
        flag = _PLANNER_TOOL_ENABLED.get(cap.name)
        if flag is None or getattr(runtime, flag, False):
            enabled.append(cap)
    return enabled


def render_planner_tool_hint(runtime: object) -> str:
    """Tell the planner which specialist tools it may name in a step's allowed_tools.

    Sourced from the Keturah manifest (single source of truth) rather than a hardcoded
    string, so adding a planner tool to the manifest makes it available automatically.
    """
    tools = planner_tools(runtime)
    if not tools:
        return ""
    lines = [
        "## Available specialist tools",
        "(Name one in a step's allowed_tools when the request warrants it.)",
    ]
    lines += [f"- {cap.name}: {cap.description}" for cap in tools]
    return "\n".join(lines)


def family_registry() -> Registry:
    """Aggregate Tirzah's manifest + any importable sibling manifests (fail-soft).

    Whatever sibling is installed in this environment self-describes into one
    federated view — the single call that returns every family tool an LLM can use.
    """
    registry = Registry([build_manifest()])
    for package in _SIBLING_PACKAGES:
        try:
            module = importlib.import_module(f"{package}.manifest")
            registry.add(module.build_manifest())
        except Exception:
            continue
    return registry


def build_manifest() -> Manifest:
    """Tirzah's capability manifest (LLM-consumable interfaces)."""
    return manifest(
        "tirzah",
        version=_tirzah_version(),
        description="Recursive memory-architecture agent: ask over memory, plan, retrieve, and "
        "dispatch specialist coherence/research and semantic annotation.",
        capabilities=[
            capability(
                "ask",
                "Answer a question over Tirzah's memory. Returns a 3-channel result: a clean answer "
                "string, structured processEvents (trace), and ids (traceId/sessionId/messageId/"
                "requestId). Conversation recall + recursive planning apply.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "the user's question"},
                        "session_id": {"type": "string", "description": "conversation id for continuity"},
                        "retrieval_mode": {"type": "string", "enum": ["direct", "agentic", "deep"]},
                        "recursive_planning": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "processEvents": {"type": "array"},
                        "traceId": {"type": "string"},
                        "sessionId": {"type": "string"},
                    },
                },
                tags=["qa", "memory", "primary"],
            ),
            _specialist_capability(),
            _semantic_capability(),
            capability(
                "capabilities",
                "List Tirzah's LLM-consumable interfaces (this manifest); ?format=mcp for the MCP view.",
                kind="resource",
                tags=["discovery"],
            ),
        ],
    )
