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

import json
import re
from typing import Any

from tirzah.retrieval.queries import (
    expand_graph_paths,
    node_context,
    node_identity,
    search_nodes,
)

# Bound for any integer argument coming from the model (limits, depth).
MAX_PRIMITIVE_INT = 50

# Default deterministic stop threshold: stop a round when the share of newly-seen
# candidates falls below this (low novelty).
DEFAULT_MIN_NOVELTY = 0.1


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


# --------------------------------------------------------------------------- #
# Deep retrieval session — the Python-authoritative state and deterministic stop
# logic the orchestrator loop is built on (ADR-020). The LLM never owns this.
# --------------------------------------------------------------------------- #


class DeepRetrievalSession:
    """Holds the authoritative state for one deep retrieval session:
    session-scoped exclusion of already-returned chunks, the accumulating
    "useful chunks" bucket, and the deterministic stop signals (novelty,
    no-new-candidates, diminishing returns, and a hard iteration cap).
    """

    def __init__(self, *, max_iterations: int, min_novelty: float = DEFAULT_MIN_NOVELTY) -> None:
        self.max_iterations = max(1, int(max_iterations))
        self.min_novelty = min_novelty
        self.exclusion_ids: set[str] = set()
        self.useful_chunks: list[dict[str, Any]] = []
        self._useful_ids: set[str] = set()
        self.iterations = 0
        self.round_log: list[dict[str, Any]] = []

    def filter_new(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop candidates already returned this session and register the rest as
        seen. Returns only the new (session-deduped) candidates."""
        new: list[dict[str, Any]] = []
        for candidate in candidates:
            nid = node_identity(candidate)
            if not nid or nid in self.exclusion_ids:
                continue
            self.exclusion_ids.add(nid)
            new.append(candidate)
        return new

    def add_useful(self, items: list[dict[str, Any]]) -> None:
        """Add kept chunks to the useful-chunks bucket (deduped by node identity)."""
        for item in items:
            nid = node_identity(item)
            if nid and nid not in self._useful_ids:
                self._useful_ids.add(nid)
                self.useful_chunks.append(item)

    def record_round(self, *, new_count: int, total_count: int, best_score: float = 0.0) -> None:
        """Record one round's outcome and advance the iteration counter."""
        self.iterations += 1
        novelty = (new_count / total_count) if total_count else 0.0
        self.round_log.append(
            {
                "new": new_count,
                "total": total_count,
                "novelty": round(novelty, 4),
                "best_score": float(best_score),
            }
        )

    def should_stop(self) -> tuple[bool, str | None]:
        """Deterministic stop decision. Order: hard cap, then no-new, low-novelty,
        diminishing returns. Returns (stop, reason)."""
        if not self.round_log:
            return (False, None)
        if self.iterations >= self.max_iterations:
            return (True, "max_iterations")
        last = self.round_log[-1]
        if last["new"] == 0:
            return (True, "no_new_candidates")
        if last["novelty"] < self.min_novelty:
            return (True, "low_novelty")
        if self._diminishing_returns():
            return (True, "diminishing_returns")
        return (False, None)

    def _diminishing_returns(self, window: int = 2, min_gain: float = 1e-6) -> bool:
        if len(self.round_log) <= window:
            return False
        recent = self.round_log[-(window + 1):]
        if all(r["best_score"] <= 0 for r in recent):
            return False  # no score signal to judge improvement by
        gains = [recent[i + 1]["best_score"] - recent[i]["best_score"] for i in range(len(recent) - 1)]
        return all(g <= min_gain for g in gains)


# --------------------------------------------------------------------------- #
# The orchestrator loop (ADR-020 §4). Python-authoritative; the two LLM seams
# (planner, triager) are injected so the orchestration is testable without a
# model. The real prompt/parse planner + triager, synthesis, and the
# `retrieval_mode == "deep"` wiring are built on top of this.
# --------------------------------------------------------------------------- #


def _pages(items: list[Any], size: int):
    for i in range(0, len(items), max(1, size)):
        yield items[i : i + size]


def _as_candidates(results: Any) -> list[dict[str, Any]]:
    """Normalise a primitive's result into a flat list of node-like dicts (those
    carrying a node identity), so search results and context results both feed the
    shortlist."""
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict) and node_identity(r)]
    if isinstance(results, dict):
        out: list[dict[str, Any]] = []
        if node_identity(results):
            out.append(results)
        node = results.get("node")
        if isinstance(node, dict) and node_identity(node):
            out.append(node)
        for key in ("children", "nodes", "results"):
            for r in results.get(key) or []:
                if isinstance(r, dict) and node_identity(r):
                    out.append(r)
        return out
    return []


def run_deep_retrieval(
    db: Any,
    query: str,
    *,
    config: Any,
    planner,
    triager,
    query_embedding: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one deep retrieval session.

    Loop: **plan** (`planner`, the LLM) -> **validate + execute** (the primitive
    menu) -> **dedup + shortlist** (Python, session-scoped) -> **triage**
    (`triager`, the LLM, paged) -> **stop** (`DeepRetrievalSession`, deterministic).

    - `planner(plan_context) -> decision`: returns `{"action": "stop"}` or
      `{"primitive": name, "args": {...}}`.
    - `triager(query, page) -> kept`: returns the subset of the page to keep.

    Returns the gathered `useful_chunks` (for synthesis), the round log, and a
    trace. Guaranteed to terminate (stop signals + invalid-plan cap + hard cap).
    """
    rc = config.retrieval
    session = DeepRetrievalSession(max_iterations=rc.deep_max_iterations)
    trace: list[dict[str, Any]] = []
    invalid_attempts = 0
    hard_cap = 2 * session.max_iterations + 2

    for _ in range(hard_cap):
        plan_context = {
            "query": query,
            "iteration": session.iterations,
            "useful_count": len(session.useful_chunks),
            "seen_count": len(session.exclusion_ids),
            "primitives": allowed_primitive_specs(),
            "rounds": session.round_log,
        }
        decision = planner(plan_context) or {}
        if decision.get("action") == "stop":
            trace.append({"step": "stop", "reason": "planner_stop"})
            break

        name = decision.get("primitive")
        raw_args = decision.get("args") or {}
        try:
            results = run_primitive(
                db, name, raw_args, query_embedding=query_embedding, identity=identity
            )
        except PrimitiveError as exc:
            invalid_attempts += 1
            trace.append({"step": "invalid_plan", "primitive": name, "error": str(exc)})
            if invalid_attempts > session.max_iterations:
                trace.append({"step": "stop", "reason": "repeated_invalid_plans"})
                break
            continue  # bounded repair: let the planner try again

        candidates = _as_candidates(results)
        new = session.filter_new(candidates)
        shortlist = new[: max(1, rc.deep_shortlist_size)]
        kept: list[dict[str, Any]] = []
        for page in _pages(shortlist, rc.deep_page_size):
            kept.extend(triager(query, page) or [])
        session.add_useful(kept)
        session.record_round(new_count=len(new), total_count=len(candidates))
        trace.append({"step": "search", "primitive": name, "new": len(new), "kept": len(kept)})

        stop, reason = session.should_stop()
        if stop:
            trace.append({"step": "stop", "reason": reason})
            break

    return {
        "useful_chunks": session.useful_chunks,
        "rounds": session.round_log,
        "trace": trace,
    }


# --------------------------------------------------------------------------- #
# Real planner + triager — the two LLM seams, built on the answer adapter.
# Prompt-build + parse are separate from the model call so both are testable;
# the model output is treated as hostile input (parse failures degrade safely).
# --------------------------------------------------------------------------- #


def _extract_json(text: str) -> Any:
    """Best-effort: pull the first JSON value out of a model response (tolerating
    code fences and surrounding prose). Returns the parsed value or None."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"[{\[]", stripped):
        try:
            parsed, _ = decoder.raw_decode(stripped[match.start():])
            return parsed
        except json.JSONDecodeError:
            continue
    return None


def build_deep_planner_prompt(query: str, plan_context: dict[str, Any]) -> str:
    return (
        "You are a retrieval planner. Choose ONE primitive to call next, or stop "
        "when further search would add little.\n"
        f"User query: {query}\n"
        f"Round: {plan_context.get('iteration')}; useful chunks kept: "
        f"{plan_context.get('useful_count')}; already-seen nodes: "
        f"{plan_context.get('seen_count')}.\n"
        "Available primitives (pick exactly one):\n"
        + json.dumps(plan_context.get("primitives", []), indent=2)
        + '\n\nReply with ONLY a JSON object, one of:\n'
        '  {"action": "stop"}\n'
        '  {"primitive": "<name>", "args": { ... }, "rationale": "<why>"}\n'
    )


def parse_deep_decision(text: str) -> dict[str, Any]:
    """Parse a planner response into a decision the orchestrator understands.
    A `stop` is honoured; a usable primitive call is returned as-is; anything
    unparseable becomes an unknown-primitive call so the loop's bounded
    invalid-plan repair handles it (rather than silently stopping)."""
    data = _extract_json(text)
    if isinstance(data, dict) and data.get("action") == "stop":
        return {"action": "stop"}
    if isinstance(data, dict) and isinstance(data.get("primitive"), str) and data["primitive"]:
        args = data.get("args")
        return {"primitive": data["primitive"], "args": args if isinstance(args, dict) else {}}
    return {"primitive": "__unparseable__", "args": {}}


def build_deep_triage_prompt(query: str, page: list[dict[str, Any]]) -> str:
    items = [
        {
            "node_id": node_identity(c),
            "title": c.get("title"),
            "preview": c.get("text_preview") or c.get("text"),
        }
        for c in page
    ]
    return (
        "Keep only the candidate chunks genuinely relevant to the query.\n"
        f"Query: {query}\n"
        "Candidates:\n" + json.dumps(items, indent=2)
        + '\n\nReply with ONLY a JSON array of the node_id strings to KEEP, '
        'e.g. ["id1", "id3"]. Keep none with [].'
    )


def parse_deep_triage(text: str, page: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = _extract_json(text)
    if not isinstance(data, list):
        return []
    keep = {str(x) for x in data if isinstance(x, (str, int))}
    return [c for c in page if node_identity(c) in keep]


def _adapter_answer(adapter: Any, prompt_text: str) -> str:
    result = adapter.answer({"prompt_text": prompt_text})
    return result.get("answer", "") if isinstance(result, dict) else str(result)


def make_planner(adapter: Any):
    """Wrap an answer adapter into a `planner(plan_context) -> decision`."""

    def planner(plan_context: dict[str, Any]) -> dict[str, Any]:
        prompt = build_deep_planner_prompt(plan_context.get("query", ""), plan_context)
        return parse_deep_decision(_adapter_answer(adapter, prompt))

    return planner


def make_triager(adapter: Any):
    """Wrap an answer adapter into a `triager(query, page) -> kept`."""

    def triager(query: str, page: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not page:
            return []
        prompt = build_deep_triage_prompt(query, page)
        return parse_deep_triage(_adapter_answer(adapter, prompt), page)

    return triager
