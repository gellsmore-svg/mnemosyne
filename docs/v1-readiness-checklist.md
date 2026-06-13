# V1 Readiness Checklist

Date: 2026-06-13

Status: active delivery checklist. The V1 boundary is defined in `docs/build-roadmap.md` as the local memory workbench release.

Use this file to track whether the implementation can be called V1-ready. A checked item must be supported by code, tests, or a repeatable smoke command. Do not mark an item complete from intent alone.

## Completion Gates

| Gate | Status | Evidence | Remaining work |
|---|---|---|---|
| `tirzah db-ping` verifies local MongoDB connectivity. | Done | CLI command and smoke history exist. | Re-run before release tag. |
| Fresh text/Markdown source can be staged, processed, archived, and inspected through CLI and web paths. | Mostly done | CLI ingestion, queue processing, upload-source web path, document/tree/node inspection commands and tests exist. | Run one clean release-candidate smoke through both CLI and web. |
| Duplicate and failed ingestion paths move files to expected dead-letter locations. | Done | Worker tests and restart smoke cover duplicate and failed movement. | Re-run release-candidate smoke. |
| Profile-backfill status reports absent, partial, blocked, or ready coverage. | Done | Ingestion status, profile jobs, and web summaries are implemented and tested. | Re-run against a small clean fixture corpus. |
| Profiled source can produce semantic-edge candidates, and candidates can be accepted or rejected from CLI and web UI. | Done | CLI and FastAPI/web review endpoints, queueing, accept/reject, and graph-edge promotion tests exist. | Re-run one end-to-end candidate review smoke. |
| Direct retrieval ask returns a saved answer with readable activity log and source/context diagnostics. | Done | `ask`, exchange persistence, activity reports, direct retrieval diagnostics, and web ask tests exist. | Re-run with mock adapter and one local model if available. |
| Agentic retrieval ask exposes planner/tool trace. | Done | Agentic interaction tests cover planner tools, failures, fallback, and structured context document. | Re-run with mock adapter and one local model if available. |
| Generated output can become unreviewed memory and then be explicitly endorsed or rejected. | Done | Output ingestion and endorsement tests cover queue processing and review updates. | Re-run one generated-output review smoke. |
| Active document references support follow-up prompts such as "this document" in the same session. | Done | Active document persistence, vocabulary, fallback, and focused answer-flow coverage exist. | Re-run release-candidate smoke. |
| CLI and web UI expose inspection for documents, nodes, sessions, exchanges, active documents, semantic candidates, graph edges, profile jobs, and process runs. | Mostly done | CLI commands and FastAPI endpoints exist across these areas. | Audit README command list and web navigation against this exact list. |
| Default web UI does not require reading raw JSON for normal use. | Mostly done | Work mode, developer toggle, and readable logs are implemented. | Do one release-candidate UI pass in default work mode. |
| Full automated tests pass. | Done | Latest run: `474 passed`. | Re-run before release tag. |

## V1 Punch List

1. Audit README against the V1 CLI/web inspection surface.
2. Create a small release-candidate smoke corpus and document the exact smoke sequence.
3. Run the release-candidate smoke sequence through CLI and web.
4. Re-run the full test suite.
5. Tag V1 only after the checklist is complete and the working tree is clean.

## Release-Candidate Smoke Sequence Draft

Use a tiny text/Markdown fixture that is not already in MongoDB.

1. Verify database:

```bash
.venv/bin/tirzah db-ping
```

2. Ingest and inspect:

```bash
.venv/bin/tirzah ingest-one <fixture.md> --label v1_smoke
.venv/bin/tirzah list-docs --format text --limit 5
.venv/bin/tirzah show-doc <document_id>
.venv/bin/tirzah show-tree <document_id>
```

3. Ask direct and agentic questions:

```bash
.venv/bin/tirzah ask "What is this smoke document about?" --adapter mock
.venv/bin/tirzah ask "What is this smoke document about?" --adapter mock --retrieval-mode agentic --json
```

4. Backfill profiles and review one candidate if available:

```bash
.venv/bin/tirzah queue-profile-backfill --label v1_smoke --limit 20
.venv/bin/tirzah process-profile-backfill --max-batches 1
.venv/bin/tirzah enqueue-profile-semantic-batch --label v1_smoke --dry-run --format text
```

5. Confirm session and active-document continuity:

```bash
.venv/bin/tirzah ask "What does this document say?" --adapter mock --session-id default --json
.venv/bin/tirzah sessions
.venv/bin/tirzah active-documents --session-id default
```

6. Run tests:

```bash
.venv/bin/pytest
```
