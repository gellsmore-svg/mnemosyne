---
type: Module
title: Web app
description: The HTTP API behind `tirzah serve` — ask/search/graph/governance/ingestion endpoints, the trace/feedback channel, plan-execution inspection — serving the built Mahlah front end as its UI.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/web/app.py
tags: [tirzah, web, api, mahlah]
timestamp: 2026-07-05T00:00:00Z
---

# Web app (`web/app.py`)

`tirzah serve` (default `http://127.0.0.1:8765/`) is **API-first**: the UI is
the built [Mahlah](https://github.com/gellsmore-svg/Mahlah) front end, installed
into `web/static` (`scripts/build_ui.sh`, or Noa's `install_tirzah_ui`); without
it the root serves a pointer page and the API stays fully live.

Endpoint families:

- **Ask & sessions** — `/api/ask` (the same traced interaction as the CLI:
  3-channel contract, planning honoured), sessions, history, continuity,
  active documents.
- **Retrieval & graph** — search, graph edges/proximity/paths, documents.
- **Ingestion & profiles** — inbox processing, upload, ingestion status,
  profile/embedding backfill jobs (analytics live in
  `ingestion/status.py` and `adapters/discovery.py`, not the view layer).
- **Governance & review** — identities, policies, process objects/runs,
  endorsements, semantic-edge candidate review.
- **Trace & feedback** — `/api/trace/sessions|events|stream` (SSE; what Mahlah's
  process panel and dev-log consume) and `/api/feedback`.
- **Planning** — `/api/plans/{id}` (revisions) and
  `/api/plan-executions[/{plan_id}]` (persisted interpretive executions).
- **Manifests** — `/api/capabilities` and the federated `/api/registry`
  (`?format=mcp`).
