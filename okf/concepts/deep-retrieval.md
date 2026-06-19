---
type: Concept
title: Deep retrieval (ADR-020)
description: A Python-orchestrated, single-query retrieval agent — the LLM plans one primitive call at a time from a fixed validated menu, Python coarse-ranks and gates a bounded shortlist, the LLM triages it, deterministic signals decide when to stop, and a synthesis model answers over the kept chunks.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/docs/retrieval-agent-design.md
tags: [tirzah, deep-retrieval, agent, adr-020]
timestamp: 2026-06-19T00:00:00Z
---

# Deep retrieval

The `deep` [retrieval mode](retrieval-modes.md) (ADR-020,
`retrieval/deep.py`) is an iterative retrieval agent for a single query, designed
to stay robust under small local context windows. **Python is the authoritative
controller**; the LLM is stateless per call and never touches Mongo directly.

The loop (`run_deep_retrieval`):

1. **Plan** — the agent model emits one structured primitive call from a **fixed,
   validated menu**: `keyword_search`, `hybrid_search`, `semantic_search`,
   `adjacent_context`, `graph_traverse`. Every plan is validated as hostile input
   with one bounded repair retry.
2. **Execute + gate** — run the primitive, apply session-scoped exclusion, then
   Python coarse-ranks/[gates](hybrid-and-semantic.md) to a bounded **shortlist**.
3. **Triage** — the LLM keeps/drops the shortlist (paged), holding no state.
4. **Stop** — deterministic signals (novelty, diminishing returns) are primary;
   `max_iterations` is the hard cap.
5. **Synthesise** — a synthesis model answers over the kept chunks (with citations),
   never the running summary.

State (exclusion set, useful-chunks bucket) lives in Python/Mongo, not the LLM
context, so it survives restarts. A future **frontier `synthesis_model` adapter**
(larger context, best final answer; no retrieval role) is the main open build item.
See the [retrieval module](../modules/retrieval.md).
