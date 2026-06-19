---
type: Concept
title: Context compilation
description: Rather than dumping raw documents, Tirzah compiles role-tagged context (focus, ancestors, siblings, descendants) around selected nodes and renders it to a token-budgeted prompt, recording what was included and what was skipped.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/retrieval/queries.py
tags: [tirzah, context, prompt, budget]
timestamp: 2026-06-19T00:00:00Z
---

# Context compilation

The core retrieval philosophy: **compile structured context, don't dump
documents.** Having selected node(s) from the [graph](graph-memory.md), Tirzah
gathers their neighbourhood with **role tags** — focus, ancestors, siblings,
descendants — preserving the source text verbatim, then renders that to a
**token-budgeted prompt** (`context_char_budget` / `prompt_token_budget` /
`reserved_response_tokens`).

This is exposed both as a pipeline behind [`ask`/`chat`](../cli/ask-chat.md) and as
discrete CLI steps — `compile-context`, `render-context`, `build-prompt` — so the
assembled context is inspectable before it reaches a model.

Each exchange also records **which chunks were included and which were
considered-but-skipped**, feeding [sessions & continuity](sessions-and-continuity.md)
and the restart-state view. Relevance gating is V1-scaffold depth; per-node "why
included" explanations are post-V1. Compilation is shared by all
[retrieval modes](retrieval-modes.md).
