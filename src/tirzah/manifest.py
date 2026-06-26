"""Tirzah's Keturah manifest — the interfaces an LLM/orchestrator can consume.

Distinct from ``tirzah.capabilities`` (which introspects the resolved *adapter*
runtime): this is the catalogue of Tirzah's LLM-callable *interfaces*. It is built
from the seam contracts Tirzah already enforces (``tirzah.coherence`` for the
specialist call, ``tirzah.semantic`` for annotation) so the published manifest and
the validated contract never drift. Exposed at ``GET /api/capabilities`` (add
``?format=mcp`` for the Model Context Protocol ``tools/list`` view).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from keturah import Manifest, capability, manifest


def _tirzah_version() -> str:
    try:
        return _pkg_version("tirzah")
    except PackageNotFoundError:
        return "0.0.0+source"


def _specialist_capability():
    from tirzah.coherence import RESULT_FIELDS, SPECIALIST_MODES

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
                "mode": {"type": "string", "enum": sorted(SPECIALIST_MODES)},
                "context": {"type": "string", "description": "the framework text to analyse"},
            },
            "required": ["query"],
        },
        output_schema={"type": "object", "properties": {field: {} for field in RESULT_FIELDS}},
        tags=["specialist", "milcah"],
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
