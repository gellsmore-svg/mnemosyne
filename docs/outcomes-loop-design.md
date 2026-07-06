# Outcomes-validation loop — design

**Status:** design, pre-build · **Date:** 2026-07-06 · **Backlog:** family #5.

## Problem

Agentic work drifts. Over a multi-step / multi-revision plan, the work
gradually stops serving the outcome it was started for — a subtly reframed
question, a tangent that becomes the main thread, a "done" that doesn't actually
satisfy what was asked. We want a mechanism that **continually pulls work back to
outcomes-based validation**, so drift is caught and corrected instead of shipped.

## What we already have (build on, don't rebuild)

- **Process Management** (`tirzah/process/`): templates (plain-text `body`),
  instances (freeze the body at bind time; append-only `trace`; lifecycle
  `active → awaiting_gate → completed/abandoned`), and **enforcement**
  (`render_process_constraint`, `reach_gate`/`resolve_gate`, `flag_deviation`,
  `record_override`, `note_plan_shaped`).
- **The planner's adapt seam** (`planning/recursive.py`): the active instance's
  text is already injected into the planning context before each plan is shaped;
  `revise_plan_recursively` is the revise loop with a `revision_decision`
  (`revise/stable/complete/blocked`).
- **Cairn `OUTCOMES`** — outcomes are already first-class in the process
  grammar; **Milcah** — a coherence validator; **Galeed** — the trace spine.

The outcomes loop is mostly *wiring these together*, plus a validation function.

## Design

### 1. Outcomes are declared, structured, and frozen

Add an optional structured field to a process template (and freeze it into the
instance, like `process_body`):

```jsonc
"outcomes": [
  {"id": "O1", "statement": "The answer cites the fatigue dataset.",
   "check": "A named dataset appears in the cited evidence."},
  {"id": "O2", "statement": "Order-effect magnitude is framed as a lens, not a prediction."}
]
"outcomes_loop": {
  "cadence": "every_revision",      // every_revision | on_complete | every_n_calls
  "n": 2,                            // for every_n_calls
  "drift_threshold": 0.34,          // 0..1 fraction of outcomes unmet/drifting to act on
  "on_drift": "reanchor_then_gate"  // log | reanchor | gate | reanchor_then_gate
}
```

Backward compatible: no `outcomes` ⇒ no loop; existing templates/instances are
unaffected. Cairn `OUTCOMES` can seed these, but the **instance** is the runtime
anchor (frozen, auditable).

### 2. A validation pass — `process/outcomes.py::validate_outcomes`

Pure and testable; no live-loop dependency.

```
validate_outcomes(instance, work, *, ask=None) -> {
  ready, per_outcome: [{id, status: met|partial|unmet, evidence, note}],
  drift_score,           # fraction not met, 0..1
  drifting: bool,        # drift_score >= threshold
}
```

Two tiers:
- **Deterministic** (always): coverage checks — is each outcome referenced by a
  plan step / addressed in the accumulated artifacts/answer? Cheap, offline,
  gives a floor.
- **Judgement** (optional `ask` / Milcah): does the work *actually satisfy* each
  outcome? Milcah's coherence pressure is the natural family fit ("what would
  have to be true for this to satisfy O1?"). Never auto-fails on the model alone
  — the deterministic floor plus the model's read combine.

### 3. Where it hooks — the adapt seam, not the deep step loop

Hook at the **revise/adapt boundary** (`recursive.py`), reusing existing
machinery — lowest-risk, highest-leverage:

- After each revision (per `cadence`), run `validate_outcomes` over the work so
  far.
- **On drift** (`drift_score >= threshold`), per `on_drift`:
  - `log` — emit a Galeed drift event only.
  - `reanchor` — inject a re-anchoring constraint into the *next* planner call
    (reusing the constraint-injection seam): *"You are drifting from O1: the work
    so far does X; re-align the next steps to satisfy O1."*
  - `gate` — raise an `awaiting_gate` (reuse `reach_gate`) for human review.
  - `reanchor_then_gate` — reanchor first; if still drifting after the next
    revision, gate.
- **Block premature completion**: a `revision_decision == "complete"` is only
  honoured when outcomes are met (or a human `record_override`s). This is the
  core anti-drift guarantee.

### 4. Trace + events (Galeed)

`process.outcomes.validated`, `process.outcomes.drift`,
`process.outcomes.reanchored`, `process.outcomes.met`, all also appended to the
instance trace — so a run's outcome-alignment history is auditable in Mizpah.

### 5. Loop-authoring interface

A single-file FastAPI composer (the Cairn view-composer pattern), reusing the
process template store: author outcomes (statement + check), pick cadence /
threshold / on-drift action, preview how it would gate a sample, and save/attach
to a process template. Read-write, `web` extra.

## Phased build (each phase ships independently, fully tested)

- **Phase 1 — the engine (pure).** `outcomes` + `outcomes_loop` on templates/
  instances (optional, backward-compatible); `validate_outcomes` (deterministic
  + optional `ask`); Galeed event vocabulary. No live-loop wiring yet → fully
  unit-testable with fakes.
- **Phase 2 — live enforcement.** Wire into the revise/adapt seam: cadence-driven
  validation, re-anchor constraint injection, drift gate, and block-complete-
  until-met. Verified against the live stack (interpretive execution on).
- **Phase 3 — authoring + surfaces.** Loop-authoring UI, `tirzah process outcomes`
  CLI, and `/api/process/outcomes*` routes; Mahlah surfacing of drift/gates.

## Decisions taken (with rationale)

1. **Structured outcomes on the process instance** (not parsed ad hoc from
   prose): they must be stable and auditable to validate against. Cairn OUTCOMES
   seeds them; the instance freezes them.
2. **Hook at the adapt/revise seam, not the deep executor step loop** (for now):
   reuses constraint-injection + gates, avoids per-token overhead, and keeps the
   change contained. A finer per-call cadence is a later option.
3. **Two-tier validator, model-optional**: deterministic floor always runs;
   Milcah/LLM judgement augments but never auto-fails alone — consistent with the
   family's "human-grounded, model-informed" stance (cf. process evolution never
   auto-applying).
4. **Block-complete-until-met is the teeth**: re-anchoring nudges; the completion
   gate is what actually prevents shipping drifted work, with human override
   preserved.

## Open questions for sign-off

- **A** — For the drift *judgement* tier, default to **Milcah** (coherence
  pressure) or a plain LLM `ask`? (Lean: pluggable, Milcah when available, LLM
  fallback.)
- **B** — Default `on_drift` action: `reanchor_then_gate` (recommended) vs
  `reanchor` (softer, no human stop) vs `gate` (strict)?
- **C** — Build order confirm: Phase 1 (engine) first, in this PR; Phases 2–3
  follow separately.
