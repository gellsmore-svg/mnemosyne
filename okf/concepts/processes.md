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

## Assisted authoring and selection

Authoring is Tirzah-assisted: `review_process` runs model-free structural checks
(missing gate, too short, unnumbered steps) plus optional LLM clarifying
questions / findings / a suggested rewrite, and `trial_process` dry-runs a draft
against a sample task to confirm the planner places the gates it demands.
Selection is smart-but-advisory: `suggest_process` ranks templates by inferred
risk, scope, and urgency/keyword signals (deterministic, explainable), with an
optional LLM re-rank among the top candidates. The operator can always override;
the instance records `selection_reason` (manual / suggested / default) for the
audit trail.

## Evolving from usage

Processes improve from real-world data. `analyze_template_evolution` mines a
template's past instances for patterns — deviations flagged AND approved
repeatedly (fold them in), gates that keep getting rejected (unclear criteria),
a high emergency-override rate (gates too heavy), high abandonment (too onerous)
— and `propose_evolution` turns them into a revised body with a rationale
(deterministic notes, or an LLM integrated rewrite). Nothing auto-applies:
`apply_evolution` lands the approved body as a new version with provenance,
human-gated, and — because instances freeze their body at bind time — active
work is never disturbed.

