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
- **`refinement.py`** — Tirzah-assisted authoring: `review_process`
  (structural + optional model findings/questions/rewrite), `trial_process`
  (dry-run a draft against a sample task).
- **`selection.py`** — `suggest_process`: deterministic risk/scope/urgency
  ranking with an optional LLM re-rank; advisory (recorded as
  `selection_reason`).
- **`evolution.py`** — `analyze_template_evolution` / `propose_evolution` mine
  instances for patterns and propose a revised body; `apply_evolution` lands it
  as a provenance-tagged new version on human approval.

Surfaces: `/api/process/*` (17 routes), `tirzah process` CLI, Mahlah's process
bar. See the [concept](../concepts/processes.md).
