---
type: Module
title: Web app
description: A local web interface — a work-first Ask workspace for normal use, plus a developer mode that exposes retrieval traces, ingestion, and queue controls; it serves the same retrieval modes and session/continuity records as the CLI.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/web/app.py
tags: [tirzah, web, ui, ask]
timestamp: 2026-06-19T00:00:00Z
---

# Web app (`web/app.py`)

A local web UI over the same engine as the CLI (`tirzah serve`):

- **Ask workspace** — the work-first surface for normal use: ask a question, get a
  source-faithful answer, and see the [continuity / restart-state](../concepts/sessions-and-continuity.md)
  panel (including the skipped-chunk summary).
- **Developer mode** — exposes retrieval traces, ingestion, and queue controls for
  inspection.
- Advertises the available [retrieval modes](../concepts/retrieval-modes.md)
  (`direct` / `agentic` / `deep`) and drives the same
  [sessions](sessions.md) answer pipeline and persistence as the
  [CLI](../cli/index.md).

Default endpoint `http://127.0.0.1:8765/`. It is a presentation layer only — all
retrieval, compilation, and persistence live in the modules above.
