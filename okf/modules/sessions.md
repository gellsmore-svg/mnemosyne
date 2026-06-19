---
type: Module
title: Sessions
description: The answer pipeline (answer_query and its direct/agentic/deep dispatch) plus the persistence of sessions, exchanges, active documents, continuity/restart state, endorsements, and generated-output review.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/sessions/interaction.py
tags: [tirzah, sessions, interaction, continuity]
timestamp: 2026-06-19T00:00:00Z
---

# Sessions (`sessions/`)

The conversation layer — where [retrieval](retrieval.md) meets persistence:

- **`interaction.py`** — the answer pipeline. `answer_query` dispatches to
  `direct` / `agentic` / `deep` ([retrieval modes](../concepts/retrieval-modes.md)),
  runs the memory-agent loop / deep flow, builds the prompt via
  [context compilation](../concepts/context-compilation.md), calls the
  [answer adapter](adapters.md), and saves the exchange. (Intentionally monolithic
  at V1; a post-V1 refactor target.)
- **`continuity.py`** — the [restart-state](../concepts/sessions-and-continuity.md)
  snapshot per exchange (`session-continuity` / `restart-render`).
- **`active_documents.py`** — the working-set of documents per session.
- **`exchanges.py`, `registry.py`, `usage.py`** — exchange records, session
  registry, usage scoring.
- **`endorsements.py`, `output_ingestion.py`** — endorsing nodes and ingesting
  generated output back under review (the trust gate).
- **`activity_reports.py`** — readable activity logs.

Consumed by the [CLI](../cli/ask-chat.md) and the [web app](web.md).
