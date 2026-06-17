"""Deep retrieval mode — the fixed query-primitive menu (ADR-020).

This is the skeleton the deep retrieval agent sits on: a small, fixed set of
**validated** query primitives that the LLM selects among (it does not compose
raw Mongo queries). Each primitive has a JSON-style argument schema and a handler
that delegates to the existing, read-only `retrieval/queries.py` functions.

Every primitive call is validated as hostile input before dispatch
(`validate_primitive_call`), then run (`run_primitive`). The orchestrator loop
(plan -> validate -> execute -> gate/shortlist -> triage -> synthesise) is built
on top of this menu and is added separately.
"""

from __future__ import annotations

from typing import Any

from tirzah.retrieval.queries import (
    expand_graph_paths,
    node_context,
    search_nodes,
)

# Bound for any integer argument coming from the model (limits, depth).
MAX_PRIMITIVE_INT = 50


class PrimitiveError(ValueError):
    """A primitive call was malformed (unknown name, missing/invalid argument)."""


# --------------------------------------------------------------------------- #
# Handlers — each delegates to an existing read-only query function.
# Signature: handler(db, args, *, query_embedding, identity) -> result
# --------------------------------------------------------------------------- #


def _keyword_search(db: Any, args: dict[str, Any], *, query_embedding=None, identity=None):
    return search_nodes(
        db, query=args["query"], label=args.get("label"), limit=args["limit"], identity=identity
    )


def _hybrid_search(db: Any, args: dict[str, Any], *, query_embedding=None, identity=None):
    return search_nodes(
        db,
        query=args["query"],
        label=args.get("label"),
        limit=args["limit"],
        identity=identity,
        query_embedding=query_embedding,
    )


def _adjacent_context(db: Any, args: dict[str, Any], *, query_embedding=None, identity=None):
    return node_context(db, args["node_id"], child_limit=args["child_limit"])


def _graph_traverse(db: Any, args: dict[str, Any], *, query_embedding=None, identity=None):
    return expand_graph_paths(db, args["node_id"], max_depth=args["depth"], limit=args["limit"])


# --------------------------------------------------------------------------- #
# The fixed menu. `args`: name -> {type, required, [default]}.
# --------------------------------------------------------------------------- #

PRIMITIVES: dict[str, dict[str, Any]] = {
    "keyword_search": {
        "description": "Lexical search over node title/text/labels.",
        "args": {
            "query": {"type": "str", "required": True},
            "label": {"type": "str", "required": False},
            "limit": {"type": "int", "required": False, "default": 10},
        },
        "handler": _keyword_search,
    },
    "hybrid_search": {
        "description": "Lexical + vector blended search (uses the query embedding when available).",
        "args": {
            "query": {"type": "str", "required": True},
            "label": {"type": "str", "required": False},
            "limit": {"type": "int", "required": False, "default": 10},
        },
        "handler": _hybrid_search,
    },
    "adjacent_context": {
        "description": "The node plus its document, parent, and children.",
        "args": {
            "node_id": {"type": "str", "required": True},
            "child_limit": {"type": "int", "required": False, "default": 20},
        },
        "handler": _adjacent_context,
    },
    "graph_traverse": {
        "description": "Follow graph relationships from a node, up to a bounded depth.",
        "args": {
            "node_id": {"type": "str", "required": True},
            "depth": {"type": "int", "required": False, "default": 2},
            "limit": {"type": "int", "required": False, "default": 10},
        },
        "handler": _graph_traverse,
    },
}

_COERCE = {"str": str, "int": int}


def allowed_primitive_specs() -> list[dict[str, Any]]:
    """The menu as plain data (no handlers), for the agent prompt / introspection."""
    return [
        {
            "name": name,
            "description": prim["description"],
            "args": {arg: dict(spec) for arg, spec in prim["args"].items()},
        }
        for name, prim in PRIMITIVES.items()
    ]


def validate_primitive_call(name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
    """Validate + normalise a primitive call. Returns the cleaned args, or raises
    PrimitiveError. Unknown args are ignored; required args must be present;
    integers are coerced and bounded."""
    if name not in PRIMITIVES:
        raise PrimitiveError(
            f"unknown primitive '{name}'. Allowed: {', '.join(sorted(PRIMITIVES))}."
        )
    raw_args = raw_args or {}
    if not isinstance(raw_args, dict):
        raise PrimitiveError(f"{name}: arguments must be an object.")
    schema = PRIMITIVES[name]["args"]
    args: dict[str, Any] = {}
    for pname, pspec in schema.items():
        if pname in raw_args and raw_args[pname] is not None:
            try:
                value = _COERCE[pspec["type"]](raw_args[pname])
            except (TypeError, ValueError):
                raise PrimitiveError(f"{name}: argument '{pname}' must be {pspec['type']}.")
            if pspec["type"] == "int":
                value = max(1, min(value, MAX_PRIMITIVE_INT))
            args[pname] = value
        elif pspec["required"]:
            raise PrimitiveError(f"{name}: missing required argument '{pname}'.")
        elif "default" in pspec:
            args[pname] = pspec["default"]
    return args


def run_primitive(
    db: Any,
    name: str,
    raw_args: dict[str, Any] | None,
    *,
    query_embedding: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
) -> Any:
    """Validate then dispatch a primitive call to its read-only handler."""
    args = validate_primitive_call(name, raw_args)
    return PRIMITIVES[name]["handler"](db, args, query_embedding=query_embedding, identity=identity)
