# V1 Readiness Checklist

Date: 2026-06-13

Status: active delivery checklist. The V1 boundary is defined in `docs/build-roadmap.md` as the local memory workbench release. The release-candidate smoke procedure is `docs/v1-release-candidate-smoke.md`.

Use this file to track whether the implementation can be called V1-ready. A checked item must be supported by code, tests, or a repeatable smoke command. Do not mark an item complete from intent alone.

## Completion Gates

| Gate | Status | Evidence | Remaining work |
|---|---|---|---|
| `tirzah db-ping` verifies local MongoDB connectivity. | Done | CLI command and smoke history exist. | Re-run before release tag. |
| Fresh text/Markdown source can be staged, processed, archived, and inspected through CLI and web paths. | Mostly done | CLI ingestion, queue processing, upload-source web path, document/tree/node inspection commands and tests exist; `docs/v1-release-candidate-smoke.md` defines the fixture-backed smoke; CLI smoke pass completed on 2026-06-13. | Run one clean release-candidate web smoke pass. |
| Duplicate and failed ingestion paths move files to expected dead-letter locations. | Done | Worker tests and restart smoke cover duplicate and failed movement. | Re-run release-candidate smoke. |
| Profile-backfill status reports absent, partial, blocked, or ready coverage. | Done | Ingestion status, profile jobs, and web summaries are implemented and tested. | Re-run against a small clean fixture corpus. |
| Profiled source can produce semantic-edge candidates, and candidates can be accepted or rejected from CLI and web UI. | Done | CLI and FastAPI/web review endpoints, queueing, accept/reject, and graph-edge promotion tests exist. | Re-run one end-to-end candidate review smoke. |
| Direct retrieval ask returns a saved answer with readable activity log and source/context diagnostics. | Done | `ask`, exchange persistence, activity reports, direct retrieval diagnostics, and web ask tests exist. | Re-run with mock adapter and one local model if available. |
| Agentic retrieval ask exposes planner/tool trace. | Done | Agentic interaction tests cover planner tools, failures, fallback, and structured context document. | Re-run with mock adapter and one local model if available. |
| Generated output can become unreviewed memory and then be explicitly endorsed or rejected. | Done | Output ingestion and endorsement tests cover queue processing, targeted session/job processing, review updates, and the 2026-06-13 CLI smoke endorsement. | Re-run web generated-output review smoke. |
| Active document references support follow-up prompts such as "this document" in the same session. | Done | Active document persistence, vocabulary, fallback, and focused answer-flow coverage exist. | Re-run release-candidate smoke. |
| CLI and web UI expose inspection for documents, nodes, sessions, exchanges, active documents, semantic candidates, graph edges, profile jobs, and process runs. | Done | README command list covers the V1 CLI inspection surface; developer-mode web UI exposes Browse/Ingestion inspection panels, active documents, graph inspection, process runs, profile jobs, and semantic review. | Re-run release-candidate UI pass. |
| Default web UI does not require reading raw JSON for normal use. | Mostly done | Work mode, developer toggle, and readable logs are implemented. | Do one release-candidate UI pass in default work mode. |
| Full automated tests pass. | Done | Latest run: `481 passed`. | Re-run before release tag. |

## V1 Punch List

1. Create a small release-candidate smoke corpus and document the exact smoke sequence. Done: `tests/fixtures/v1-smoke-source-template.md` and `docs/v1-release-candidate-smoke.md`.
2. Run the release-candidate smoke sequence through CLI and web. CLI pass complete on 2026-06-13; web pass remains.
3. Re-run the full test suite. Latest run complete: `481 passed`.
4. Tag V1 only after the checklist is complete and the working tree is clean.

## Release-Candidate Smoke Sequence

Run `docs/v1-release-candidate-smoke.md` from a clean working tree when preparing the V1 tag.
