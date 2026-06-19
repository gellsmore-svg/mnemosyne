---
type: CLI Command
title: Governance & sessions
description: Seed and inspect agent identities, trust profiles, governance policies, and process objects/runs; manage sessions, active documents, and continuity/restart state; and run the endorsement / generated-output review gate.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/cli.py
tags: [tirzah, cli, governance, sessions, endorsement]
timestamp: 2026-06-19T00:00:00Z
---

# Governance & sessions

The [governance](../concepts/governance.md) and
[sessions/continuity](../concepts/sessions-and-continuity.md) surfaces:

- **Identities & trust** — `agent-identities` / `agent-identity`,
  `trust-weighting-profiles` / `trust-weighting-profile`, `trust-diagnostic`.
- **Process governance** — `governance-policies` / `governance-policy`,
  `process-objects` / `process-object`, `process-runs` / `process-run`,
  `start-process-run` / `update-process-run` (observational in V1).
- **Sessions & continuity** — `create-session`, `sessions`, `active-documents`,
  `session-continuity`, `restart-render` (the [restart-state](../concepts/sessions-and-continuity.md)
  snapshot).
- **Trust gate** — `output-ingestion` / `process-output-ingestion`,
  `review-generated-output`, `endorse-node`: ingest model output back under review
  and explicitly endorse what becomes trusted memory.

These records are also surfaced in the [web Ask workspace](../modules/web.md).
