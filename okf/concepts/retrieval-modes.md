---
type: Concept
title: Retrieval modes — direct, agentic, deep
description: Three selectable strategies for turning a query into answer context — direct lexical/hybrid focus selection, an agentic tool-using loop, and the deep Python-orchestrated retrieval agent.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/sessions/interaction.py
tags: [tirzah, retrieval, modes, direct, agentic, deep]
timestamp: 2026-06-19T00:00:00Z
---

# Retrieval modes

`runtime.retrieval_mode` (CLI `--retrieval-mode`) selects how a query becomes
answer context:

- **`direct`** (default) — pick a focus node by lexical (optionally
  [hybrid](hybrid-and-semantic.md)) ranking, [compile context](context-compilation.md)
  around it, and answer in one model call. Fast and predictable.
- **`agentic`** — an iterative **memory-agent loop**: a first model call selects
  read-only retrieval tools (`search_nodes`, `compile_context`, `list_documents`)
  before the answer call, so the model can gather what it needs.
- **`deep`** — the [deep-retrieval agent](deep-retrieval.md) (ADR-020): a
  Python-orchestrated loop over a fixed, validated primitive menu
  (plan → execute → triage → synthesise). Highest quality, more model calls; opt-in.

All three answer over the same [graph memory](graph-memory.md) and share the
[adapters](../modules/adapters.md) and [hybrid ranking](hybrid-and-semantic.md).
`deep` multiplies model calls, so it is not the global default. Implemented in the
[sessions module](../modules/sessions.md) (`answer_query` dispatch) and the
[retrieval module](../modules/retrieval.md).
