---
type: Module
title: process
description: Human-defined Processes — templates (versioned), instances (bound + traced), enforcement (planner constraint, gates, deviations, override), and retrospective (review, metrics, history).
resource: https://github.com/gellsmore-svg/tirzah/tree/main/src/tirzah/process
tags: [tirzah, module, process]
timestamp: 2026-07-05T00:00:00Z
---

# process

- **`templates.py`** — versioned templates (`process_templates`): create /
  revise (append-only history) / latest / versions / list, and idempotent
  preset seeding (Governed / Fluid / Emergency). Body is plain text.
- **`instances.py`** — `process_instances`: `start_instance` (freezes the
  process body at bind time), lifecycle (active → awaiting_gate →
  completed/abandoned), append-only trace, `active_instance_for_session`.
- **`enforcement.py`** — `render_process_constraint` (the planner guide),
  gate detection from prose, `reach_gate`/`resolve_gate`,
  `flag_deviation`/`resolve_deviation`, `record_override`; best-effort Galeed
  events.
- **`retrospective.py`** — `build_retrospective`, `usage_metrics`,
  `similar_task_history`.

Surfaces: `/api/process/*` (17 routes), `tirzah process` CLI, Mahlah's process
bar. See the [concept](../concepts/processes.md).
