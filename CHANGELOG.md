# Changelog

All notable changes to Tirzah are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Outcomes-validation loop: Milcah judgement tier. `validate_outcomes` gained a
  generic `judge` callable ((outcomes, work_text) → {id: status}); a loop with
  `judge: "milcah"` frames each outcome as a claim, pressure-tests it via Milcah
  coherence, and maps the verdict (objections + confidence + terminal_reason) to
  met/partial/unmet (`make_milcah_judge` / `verdict_to_status`). Best-effort:
  falls back to the deterministic floor when Milcah is unavailable, and the gate
  still requires the floor to agree.
- Outcomes-validation loop, phase 3 (authoring surfaces + judgement tier): a
  single-file authoring composer at `GET /process/outcomes` (author outcomes +
  loop, preview drift against a sample answer, save to a template); process API
  routes (`GET`/`PUT /api/process/templates/{id}/outcomes`,
  `POST /api/process/instances/{id}/outcomes/validate`,
  `POST /api/process/outcomes/preview`); a `tirzah process outcomes
  show|set|validate` CLI; and an authorable `judge` field on the loop
  (`deterministic` default, or `llm` to wire the model judgement tier from the
  process `default_ask`, best-effort with the deterministic floor as fallback).
- Outcomes-validation loop, phase 2 (live enforcement): the recursive planner now
  drives an armed outcomes loop. `planning/outcomes_control.py` validates the
  accumulated work after each execution and, on drift, **re-anchors** the next
  revision (names the drifted outcomes back to the planner) and **gates**
  premature completion — when a revision proposes complete/stabilise while the
  work still drifts, it raises a human gate (resolved with the ordinary process
  gate approve/reject; approve accepts despite drift) and sets
  `result["outcomes_gate"]`. The gate fires only when the deterministic floor
  also finds an outcome unmet (never on a model judgement alone). Fully guarded:
  no change to the planner path unless a template author armed a loop.
- Outcomes-validation loop, phase 1 (the pure engine): process templates and
  instances can declare structured `outcomes` (+ an `outcomes_loop` cadence /
  drift-threshold / on-drift config), frozen into the instance at bind time.
  `process.validate_outcomes` scores accumulated work against them with a
  deterministic keyword-coverage floor plus an optional model judgement tier
  (Milcah/LLM), returning per-outcome status and a drift score;
  `render_reanchor_constraint` names drifted outcomes for the planner. Not yet
  wired into the live revise loop (phase 2). Backward-compatible: no outcomes ⇒
  no loop. Design: `docs/outcomes-loop-design.md`.

### Fixed
- Closed the v1.4.0 review bugs around QUEUE normalization, specialist tool
  dispatch, process gate resume wiring, plan persistence warnings, Mahalath and
  Milcah failure visibility, retry-safe retrieval effects, ITERATE blocked
  propagation, runtime capability drift, web API guardrails, and migration
  coverage.

## [1.4.0] - 2026-07-06

### Added
- **Human-defined Processes** (`tirzah/process/`): versioned plain-text process
  templates (history preserved), instances that bind a template version to a
  task (process body frozen at bind time), and three seeded presets
  (Governed / Fluid / Emergency). The active process conditions the interpretive
  planner as its top-level guide; gates pause the instance (resumable),
  deviations are flagged for approval, emergency override requires a
  justification. Retrospectives, usage metrics, and similar-task history.
  Surfaced via `/api/process/*` routes, a `tirzah process` CLI, and Mahlah's
  process bar. Process actions emit onto the Galeed spine.
- **Process authoring + selection assistants**: `review_process`
  (structural + optional LLM findings, clarifying questions, suggested
  rewrite), `trial_process` (dry-run a draft against a sample task), and
  `suggest_process` (recommend a process from task risk/scope/urgency, with an
  optional LLM re-rank) — via `/api/process/{review,trial,suggest}`, the
  `tirzah process {review,trial,suggest}` CLI, and the process bar (auto-suggest
  on open, "Review with Tirzah").
- **Template auto-evolution**: `analyze_template_evolution` / `propose_evolution`
  mine a template's instances (recurring approved deviations, gate rejections,
  override/abandonment rates) and propose a revised body with rationale;
  `apply_evolution` lands it as a new version with provenance on human approval
  (active instances unaffected). Via `/api/process/templates/{id}/evolution|evolve`
  and `tirzah process evolve <id> [--apply]`.

### Added
- **Interpretive Cairn plan execution (SPEC §4.6)** — gated by
  `TIRZAH_PLAN_INTERPRETIVE_EXECUTION_ENABLED`: the recursive planner's machine
  plan is walked step-by-step (dependency order, allowed_tools gating) with the
  full construct set — ITERATE, DECISION, PARALLEL/MERGE (isolated or shared
  scopes, CONCURRENT threads), RETRY with backoff, ERROR fallback recovery,
  AWAIT/SERVICE, BREAK/CONTINUE — plus **mid-step revision**
  (`plan_mid_revision_enabled`, default on): the plan adapts after each
  completed call. Executions persist for resume (`plan_executions`;
  `/api/plan-executions`, `tirzah plan-executions`).
- **Live plan streaming** — with the request tracer attached, every plan trace
  entry publishes to the bus the moment it happens; Mahlah's process panel
  shows the running plan (construct badges) live, auto-expanding on the first
  plan event.
- **LLM debugging capture** — every `answer_adapter` step records its COMPLETE
  prompt and answer into galeed's `llm_calls` (deep mode included via
  `build_synthesis_prompt`), correlated to the live trace/session. Viewable in
  `galeed trace` / Mizpah's LLM Calls tab.
- **Answer pipeline phases** — retrieval and synthesis split
  (`sessions/answer_phases.py`) so plans can drive them as separate steps.

### Changed
- Analytics helpers moved out of `web/app.py` into `adapters/discovery.py` and
  `ingestion/status.py` (no behaviour change).
- Depends on **cairn-lang** (renamed distribution; `import cairn` unchanged).

### Fixed
- Three live-Mongo defects in the plan path: pymongo Database truth-testing,
  PosixPath in persisted runtime config (bson), and the interpretive result
  overwriting the phases' trace (which dropped the answer capture).

### Changed
- **Web UI moved to a `tirzah[web]` extra** — `fastapi`/`uvicorn` are no longer core
  dependencies, so library/CLI installs stay lean and never import the web stack
  unless serving. `tirzah serve` prints a clear hint if the extra isn't installed.
  (A precursor to splitting the UI out once the HTTP API is a versioned contract.)
- The Mahalath seam is now configurable from the environment
  (`MAHALATH_ENABLED` / `MAHALATH_MONGO_URI` / `MAHALATH_MONGO_DB` /
  `MAHALATH_STRICT`), so a missing config file can no longer silently disable it.
- `mahalath_strict` now defaults to **true** (drop fuzzy approximations; keep only
  confident label/exact/alias matches) — precision-grade by default.

### Added
- **`tirzah config-status`** + an adapter **capability registry** (`tirzah/capabilities.py`):
  reports the resolved runtime — active config file, selected adapters and their
  capabilities (`can_embed`/`can_answer`/`uses_mock`/`requires_hoglah`/
  `can_resolve_mpl`/…), models, embedding dims, and the Mahalath seam state, with
  explicit warnings (mock embeddings, Mahalath off, worker required). The same
  snapshot is folded into `memory-health`. Would have made the CBO mock-embeddings /
  disabled-resolver situation obvious at a glance.
- `tirzah migrate` — a consolidated, ordered, **idempotent** schema-migration
  command with a `schema_migrations` ledger (mirrors Mahalath's), gathering the
  scattered `backfill-*` one-shots; migration 1 stamps `schema_version` on legacy
  documents/trees/nodes. Supports `--status` and `--dry-run`.

## [1.3.0] - 2026-06-22

The first release after a long unversioned stretch on `main` (v1.2.0 had shipped
while `pyproject` stayed at 1.2.0). All additions are backward-compatible.

### Added
- **Tirzah→Mahalath semantic-precision seam** (`tirzah/semantic.py`): during
  retrieval, resolve the key terms of the query + context to Mahalath **MPL labels
  and senses**, inject a "Semantic Precision (MPL)" block so the answer is
  conditioned on the precise sense, and surface an "interpreted as …" line in `ask`
  output. Off by default (`mahalath_enabled`); optional, fail-soft. Symbolic labels
  only. Optional `mahalath` extra for co-install.
- **Deep retrieval mode** (ADR-020): plan → execute → triage → synthesise, with a
  real planner/triager over the answer adapter and deterministic stop signals;
  selectable as a retrieval mode.
- **Hybrid lexical + vector ranking** (ADR-020): on by default (real-corpus
  validated); wired into the direct, agentic, and deep retrieval paths.
- **Semantic search** for free-text queries.
- **Hoglah messaging transports** (Kafka / RabbitMQ / Redis) and embedding-adapter
  CLI exposure; decoupled submitter/daemon topology for answers + embeddings.
- **Session continuity**: persist prompt iterations (CLI + web), capture
  considered-but-skipped chunks, and a `.restart.md` renderer.
- **Portable configuration**: env overrides (`OLLAMA_BASE_URL`,
  `OLLAMA_EXECUTABLE`, `TIRZAH_MONGO_URI/DB`) applied even with no config file, and
  `TIRZAH_CONFIG` to set the config location independent of the working directory.

### Fixed
- `ollama_executable` now defaults to a PATH-resolved `ollama` instead of a
  hardcoded WSL path, so a fresh install is portable.
- `__version__` is now derived from package metadata, ending the
  `pyproject` vs `__init__` version mismatch.

## [1.2.0] - 2026-06-13

### Added
- Decoupled Hoglah topology for answers + embeddings (ADR-019): Tirzah is a pure
  submitter into a shared queue, with a separate worker daemon executing jobs.

## [1.1.1] - 2026-06-13
### Changed
- Same-day refinements over 1.1.0.

## [1.1.0] - 2026-06-13
### Added
- Early post-1.0.0 iteration.

## [1.0.0] - 2026-06-13
### Added
- Initial public release: local graph-based memory and retrieval for LLM
  interactions (MongoDB store, Ollama adapters, FastAPI + CLI surfaces).

[Unreleased]: https://github.com/gellsmore-svg/tirzah/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/gellsmore-svg/tirzah/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/gellsmore-svg/tirzah/releases/tag/v1.0.0
