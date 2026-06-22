# Changelog

All notable changes to Tirzah are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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

[Unreleased]: https://github.com/gellsmore-svg/tirzah/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/gellsmore-svg/tirzah/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/gellsmore-svg/tirzah/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/gellsmore-svg/tirzah/releases/tag/v1.0.0
