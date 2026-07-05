---
type: Concept
title: Interpretive plan execution
description: Cairn machine plans are walked step-by-step (SPEC §4.6) — dependency-gated, tool-gated, with the full construct set, mid-step revision after each completed call, resumable persisted executions, and live streaming into the process panel.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/planning/executor.py
tags: [tirzah, concepts, planning, cairn, interpretation]
timestamp: 2026-07-05T00:00:00Z
---

# Interpretive plan execution

Recursive request planning produces a machine-readable Cairn plan. With
`TIRZAH_PLAN_INTERPRETIVE_EXECUTION_ENABLED=true` that plan is **executed** —
walked step-by-step by an interpreter (Cairn SPEC §4.6) rather than treated as
narrative around a monolithic pipeline.

- **Ready-set walking**: a step runs only when every `depends_on` id is
  completed; construct bodies are deferred while their parent is pending.
- **Tool gating**: `CALL` steps dispatch through a handler registry constrained
  by `allowed_tools`; unknown tools block, never silently fall back.
- **Constructs**: ITERATE (bounded rounds, BREAK/CONTINUE), DECISION (branch
  select + cascade skip), PARALLEL/MERGE (isolated or shared scopes; CONCURRENT
  runs isolated branches on real threads), RETRY (bounded, whole-body re-run,
  backoff), ERROR (signal-matched fallback recovery), AWAIT/SERVICE.
- **Adaptation**: with `plan_mid_revision_enabled` (default on) the planner is
  consulted after each completed step with the new information; a "revise"
  decision swaps in the revised plan mid-flight, statuses preserved. Post-run
  revision cycles continue until the planner says stable/complete.
- **Resumability**: executions persist per step (`plan_executions`); an
  interrupted run resumes with `active` steps reset to pending and once-only
  effects deduplicated.
- **Live visibility**: with the request Tracer attached, every trace entry
  streams to the bus the moment it happens — Mahlah's process panel shows the
  running plan (construct badges) and auto-expands on the first plan event.
  Entries are marked `live` so the post-hoc bridge never duplicates them.
