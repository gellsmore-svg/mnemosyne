---
type: Module
title: planning
description: Recursive Cairn planning plus the interpretive executor — recursive.py (plan/revise), executor.py (walker + handlers), constructs.py, execution_store.py (resume), parallel_runtime.py, revision_runtime.py, context_bundle.py.
resource: https://github.com/gellsmore-svg/tirzah/tree/main/src/tirzah/planning
tags: [tirzah, module, planning, cairn]
timestamp: 2026-07-05T00:00:00Z
---

# planning

- **`recursive.py`** — creates and revises Cairn plans (planner LLM behind a
  seam), validates against `cairn.validate_plan`, saves revisions
  (`recursive_plans`), and drives `process_frontend_request`: planning off →
  plain executor; on → plan, interpret (when enabled), revise, repeat within
  `planning_max_revisions`.
- **`executor.py`** — `interpret_plan`: the §4.6 walker (see the
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
