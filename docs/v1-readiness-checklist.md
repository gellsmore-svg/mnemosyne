# V1 Readiness Checklist

Date: 2026-06-13
Last updated: 2026-06-14

Status: V1 complete. The V1 boundary is defined in `docs/build-roadmap.md` as the local memory workbench release. The release-candidate smoke procedure is `docs/v1-release-candidate-smoke.md`. V1 release tag: `v1.0.0`; current V1.x release tag: `v1.2.0`.

Completion here means the local memory workbench surface is implemented and smoke-tested. It does not mean the post-V1 hardening items are finished; see `docs/v1-known-limitations.md`.

Use this file to track whether the implementation can be called V1-ready. A checked item must be supported by code, tests, or a repeatable smoke command. Do not mark an item complete from intent alone.

## Completion Gates

| Gate | Status | Evidence | Remaining work |
|---|---|---|---|
| `tirzah db-ping` verifies local MongoDB connectivity. | Done | CLI command, automated coverage, and the 2026-06-13 CLI smoke are complete. | None. |
| Fresh text/Markdown source can be staged, processed, archived, and inspected through CLI and web paths. | Done | CLI ingestion, queue processing, upload-source web path, document/tree/node inspection commands and tests exist; `docs/v1-release-candidate-smoke.md` defines the fixture-backed smoke; CLI and web smoke passes completed on 2026-06-13. | None. |
| Duplicate and failed ingestion paths move files to expected dead-letter locations. | Done | Worker tests and restart smoke cover duplicate and failed movement. | None. |
| Profile-backfill status reports absent, partial, blocked, or ready coverage. | Done | Ingestion status, profile jobs, web summaries, and the 2026-06-13 CLI/web smoke are complete. | None. |
| Profiled source can produce semantic-edge candidates, and candidates can be accepted or rejected from CLI and web UI. | Done | CLI and FastAPI/web review endpoints, queueing, accept/reject, graph-edge promotion tests, and the 2026-06-13 smoke surface checks are complete. | None. |
| Direct retrieval ask returns a saved answer with readable activity log and source/context diagnostics. | Done | `ask`, exchange persistence, activity reports, direct retrieval diagnostics, web ask tests, and the 2026-06-13 CLI smoke are complete. | None. |
| Agentic retrieval ask exposes planner/tool trace. | Done | Agentic interaction tests cover planner tools, failures, fallback, and structured context document; the 2026-06-13 CLI smoke returned planner/tool trace data. | None. |
| Generated output can become unreviewed memory and then be explicitly endorsed or rejected. | Done | Output ingestion and endorsement tests cover queue processing, targeted session/job processing, review updates, and the 2026-06-13 CLI smoke endorsement. | None. |
| Active document references support follow-up prompts such as "this document" in the same session. | Done | Active document persistence, vocabulary, fallback, focused answer-flow coverage, and the 2026-06-13 CLI smoke are complete. | None. |
| CLI and web UI expose inspection for documents, nodes, sessions, exchanges, active documents, semantic candidates, graph edges, profile jobs, and process runs. | Done | README command list covers the V1 CLI inspection surface; developer-mode web UI exposes Browse/Ingestion inspection panels, active documents, graph inspection, process runs, profile jobs, and semantic review; 2026-06-13 CLI/web smoke passes are complete. | None. |
| Default web UI does not require reading raw JSON for normal use. | Done | Work mode, developer toggle, readable logs, and the 2026-06-13 web smoke pass are complete. | None. |
| Full automated tests pass. | Done | Latest run: `500 passed` on 2026-06-14. | None. |

## V1 Punch List

1. Create a small release-candidate smoke corpus and document the exact smoke sequence. Done: `tests/fixtures/v1-smoke-source-template.md` and `docs/v1-release-candidate-smoke.md`.
2. Run the release-candidate smoke sequence through CLI and web. Done on 2026-06-13.
3. Re-run the full test suite. Latest run complete: `500 passed` on 2026-06-14.
4. Tag V1 only after the checklist is complete and the working tree is clean. Done as `v1.0.0`.

## Release-Candidate Smoke Sequence

Run `docs/v1-release-candidate-smoke.md` from a clean working tree when preparing the V1 tag.
