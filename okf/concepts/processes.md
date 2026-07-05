---
type: Concept
title: Human-defined Processes
description: Configurable plain-text process templates that ground agentic work with the right oversight — the active process guides the planner, gates pause execution, deviations are flagged, everything is auditable.
resource: https://github.com/gellsmore-svg/tirzah/tree/main/src/tirzah/process
tags: [tirzah, concepts, process, governance, oversight]
timestamp: 2026-07-05T00:00:00Z
---

# Human-defined Processes

A **template** is a versioned, human-authored plain-text description of how work
should proceed (gates and loops stated in prose). An **instance** binds a
template *version* to a task and carries its own state + append-only audit
trace; binding the version (not just the id) means an in-flight instance keeps
its process text when the template evolves.

When a conversation runs under a process, its text is prepended to the
interpretive planner's context as the top-level guide: the planner plans within
the process and emits AWAIT gate steps where it demands approval. **Gates**
pause the instance (`awaiting_gate`, resumable on approval); **deviations** are
flagged for approval rather than silently taken; an **emergency override**
needs a justification and (per the Emergency preset) a mandatory retrospective.

Three presets span the spectrum: Governed (gates before apply/ship), Fluid
(log-only oversight), Emergency (act-first). Everything is auditable —
per-instance retrospective, usage metrics (adherence, deviation/override rates,
outcomes), and a "how were similar tasks handled?" history query. Process
actions also emit onto the Galeed spine, watchable in Mizpah.
