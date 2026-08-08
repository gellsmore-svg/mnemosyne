---
type: Module
title: planning
description: Recursive Cairn planning, Deborah framed handoff, and the interpretive executor — recursive.py, deborah_bridge.py, executor.py, constructs.py, execution_store.py, parallel_runtime.py, revision_runtime.py, context_bundle.py.
resource: https://github.com/gellsmore-svg/tirzah/tree/main/src/tirzah/planning
tags: [tirzah, module, planning, cairn]
timestamp: 2026-07-05T00:00:00Z
---

# planning

- **`recursive.py`** — creates and revises Cairn plans (planner LLM behind a
  seam), soft-validates against `deborah.validate_plan` (via deborah_bridge),
  saves revisions (`recursive_plans`), and drives `process_frontend_request`:
  planning off → plain executor; on → plan, then either Deborah framed slice
  (substrate/critique graphs) or Tirzah interpret (when enabled), revise,
  repeat within `planning_max_revisions`.
- **`deborah_bridge.py`** — Tirzah↔Deborah handoff: `to_deborah_plan`,
  `validate_against_deborah`, `is_framed_substrate_plan`, `run_framed_plan`
  → Deborah `run_substrate_slice`, `compose_estate_dispatch`.
- **`executor.py`** — `interpret_plan`: the §4.6 *agentic* walker (see the
  [concept](../concepts/interpretive-planning.md)); `build_default_handlers`
  maps tools (retrieval phases, graph/document context handlers, web fetch,
  specialist via Milcah, answer synthesis) onto CALL steps; `LiveTraceList`
  streams every trace entry through the request Tracer.
- **`constructs.py`** — the construct semantics (ownership, cascade skip,
  branch subtrees, loop control, retry/error/parallel/merge execution).
- **`execution_store.py`** — persisted executions (`plan_executions`):
  save/load/resume/finalize + compact summaries for APIs.
- **`parallel_runtime.py`** — CONCURRENT thread fan-out; isolated snapshots or
  lock-serialised shared state; a crashed branch blocks itself, not the walk.
- **`revision_runtime.py`** — mid-step revision: finish the step, consult the
  planner with the fresh information, swap in the revised plan.
- **`context_bundle.py`** — the bounded artifact map CALL handlers append
  tool results into (the execution's cross-step context).

Surfaces: `/api/plan-executions[/{plan_id}]`, `tirzah plan-executions`,
`/api/plans/{plan_id}` (revisions) — plus `plan.*` live events on the spine.
