---
type: Project
title: Tirzah
description: A locally operated, graph-based memory and retrieval layer for LLM interactions — ingest sources into a provenance-aware MongoDB graph and compile structured, source-faithful context for a local model, rather than dumping whole documents into a prompt.
resource: https://github.com/gellsmore-svg/tirzah
tags: [tirzah, memory, retrieval, graph, mongodb, ollama, local-first]
timestamp: 2026-06-19T00:00:00Z
---

# Tirzah

Tirzah is a locally operated, **graph-based memory and retrieval layer** for LLM
interactions. Instead of brute-force-loading whole documents into a prompt, it
ingests sources into a **provenance-aware graph in MongoDB** and compiles
structured, navigable, source-faithful context for a local model to answer over.

It runs entirely on local infrastructure — MongoDB for storage, Ollama for
inference (optionally routed through [Hoglah](https://github.com/gellsmore-svg/hoglah)) —
and is usable from a CLI (`tirzah`) or a web Ask workspace.

This bundle is an [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
description of Tirzah's concepts, modules, and CLI.

## Map

- **[Concepts](concepts/index.md)** — the design ideas: the graph memory model,
  the three retrieval modes, hybrid + semantic ranking, the deep-retrieval agent,
  context compilation, sessions/continuity, and governance.
- **[Modules](modules/index.md)** — the code: retrieval, ingestion, sessions,
  storage, adapters, config, and the web app.
- **[CLI](cli/index.md)** — the `tirzah` commands, grouped by purpose.
- **Planning & interpretation** — recursive Cairn request planning plus the
  interpretive executor (SPEC §4.6, `TIRZAH_PLAN_INTERPRETIVE_EXECUTION_ENABLED`):
  dependency-gated step walking with the full construct set, mid-step revision,
  resumable persisted executions, and live plan streaming into the process panel.
- **Observability** — request traces on the Galeed spine, and every answer
  adapter call's complete In→Out captured into the `llm_calls` debugging store
  (viewable via `galeed trace` / Mizpah).

## At a glance

- Storage: **MongoDB**, with an in-memory store for tests — see [storage](modules/storage.md).
- Retrieval: **direct / agentic / deep** modes; hybrid lexical+vector is on by
  default — see [retrieval modes](concepts/retrieval-modes.md).
- Inference: Ollama via pluggable [adapters](modules/adapters.md) (mock default,
  HTTP, or queued through Hoglah).
- Status: early V1 "local memory workbench"; some pieces scaffold-depth (see
  [`docs/v1-known-limitations.md`](https://github.com/gellsmore-svg/tirzah/blob/main/docs/v1-known-limitations.md)).
- License: Apache-2.0. CLI: `tirzah` (legacy `mnemosyne` alias).
