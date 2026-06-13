# V1 Release-Candidate Smoke

Date: 2026-06-13

Status: release-candidate procedure. Run this only when preparing to mark V1 complete.

This smoke uses a committed template fixture and creates a per-run copy under ignored local staging data. The copied file includes a run marker so checksum duplicate rejection does not block repeated smoke runs.

## Setup

Start from the repository root with MongoDB running and the virtualenv installed.

```bash
SMOKE_RUN="$(date +%Y%m%d%H%M%S)"
SMOKE_LABEL="v1_smoke_${SMOKE_RUN}"
SMOKE_SESSION="v1-smoke-${SMOKE_RUN}"
SMOKE_SOURCE="data/staging/v1-smoke-${SMOKE_RUN}.md"
mkdir -p data/staging
cp tests/fixtures/v1-smoke-source-template.md "${SMOKE_SOURCE}"
printf "\n\nRun marker: %s\n" "${SMOKE_RUN}" >> "${SMOKE_SOURCE}"
```

## CLI Smoke

1. Verify database connectivity.

```bash
.venv/bin/tirzah db-ping
```

2. Ingest the smoke source.

```bash
.venv/bin/tirzah ingest-one "${SMOKE_SOURCE}" --label v1_smoke --label "${SMOKE_LABEL}"
```

Record the returned `document_id` and one `node_id` from the output:

```bash
SMOKE_DOCUMENT_ID="<document_id from ingest-one output>"
SMOKE_NODE_ID="<node_id from ingest-one output>"
```

3. Inspect the ingested document.

```bash
.venv/bin/tirzah list-docs --format text --limit 10
.venv/bin/tirzah show-doc "${SMOKE_DOCUMENT_ID}"
.venv/bin/tirzah show-tree "${SMOKE_DOCUMENT_ID}"
.venv/bin/tirzah search-nodes --query "copper lantern continuity marker" --label "${SMOKE_LABEL}" --limit 5
.venv/bin/tirzah node-context "${SMOKE_NODE_ID}"
.venv/bin/tirzah compile-context "${SMOKE_NODE_ID}" --ancestor-depth 2 --sibling-window 1 --child-depth 1
.venv/bin/tirzah render-context "${SMOKE_NODE_ID}" --char-budget 1200
```

4. Ask direct and agentic questions with the mock adapter.

By default, agentic retrieval planning inherits `--adapter mock`, keeping this step fully local and deterministic. If `runtime.memory_agent_adapter` is explicitly set in local config, either set it to `mock` for this smoke or run the agentic command as a configured local-model check.

```bash
.venv/bin/tirzah ask "What is the copper lantern continuity marker?" --adapter mock --session-id "${SMOKE_SESSION}" --node-id "${SMOKE_NODE_ID}" --json
.venv/bin/tirzah ask "What is this document about?" --adapter mock --session-id "${SMOKE_SESSION}" --json
.venv/bin/tirzah ask "What retrieval tools would help inspect the copper lantern continuity marker?" --adapter mock --session-id "${SMOKE_SESSION}" --retrieval-mode agentic --json
```

5. Inspect session continuity and process state.

```bash
.venv/bin/tirzah sessions
.venv/bin/tirzah active-documents --session-id "${SMOKE_SESSION}"
.venv/bin/tirzah history --session-id "${SMOKE_SESSION}" --limit 5
.venv/bin/tirzah process-runs --session-id "${SMOKE_SESSION}" --limit 10
```

6. Backfill text similarity profiles and inspect semantic review surfaces.

```bash
.venv/bin/tirzah queue-profile-backfill --label "${SMOKE_LABEL}" --limit 20
.venv/bin/tirzah process-profile-backfill --max-batches 1
.venv/bin/tirzah profile-backfill-jobs --limit 5
.venv/bin/tirzah profile-semantic-candidates "${SMOKE_NODE_ID}" --include-same-document --limit 5
.venv/bin/tirzah enqueue-profile-semantic-batch --label "${SMOKE_LABEL}" --include-same-document --dry-run --format text
.venv/bin/tirzah semantic-edge-candidates --status pending --format text --limit 10
```

If the dry run identifies a useful candidate, queue and review it:

```bash
.venv/bin/tirzah enqueue-profile-semantic-batch --label "${SMOKE_LABEL}" --include-same-document --format text
.venv/bin/tirzah semantic-edge-candidates --status pending --format text --limit 10
.venv/bin/tirzah review-semantic-edge-candidate "<candidate_id>" --action accept --reviewer v1-smoke --note "V1 smoke review"
```

7. Inspect graph surfaces.

```bash
.venv/bin/tirzah graph-edges "${SMOKE_NODE_ID}"
.venv/bin/tirzah expand-proximity "${SMOKE_NODE_ID}" --format text
.venv/bin/tirzah expand-graph-paths "${SMOKE_NODE_ID}"
```

8. Process generated output and review if a job was queued.

```bash
.venv/bin/tirzah output-ingestion --session-id "${SMOKE_SESSION}" --limit 10
.venv/bin/tirzah process-output-ingestion
.venv/bin/tirzah review-generated-output --limit 10
.venv/bin/tirzah endorse-node "<generated_output_node_id>" --endorsement explicit_endorsed --reviewer v1-smoke --note "V1 smoke endorsement"
```

9. Run automated tests.

```bash
.venv/bin/pytest
```

## Web Smoke

Start the web UI:

```bash
.venv/bin/uvicorn tirzah.web.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

Check normal work mode:

- Ask tab is visible without Browse/Ingestion tabs.
- Prompt, Response, and Activity Log are visible.
- Normal work mode can be checked as a UI/readability pass without sending a model request. If sending an ask from work mode, use the configured default adapter/model.
- Raw JSON remains hidden unless Developer mode is enabled.

Enable Developer mode and check:

- Browse tab shows node search, active documents, graph inspection, recent documents, history, and process runs.
- Ingestion tab shows source staging, inbox processing, profile backfill, semantic-edge review, recent jobs, and ingestion status.
- Search for `copper lantern continuity marker`, focus the smoke node, and use Graph to inspect edges/proximity/paths.
- Load active documents for `${SMOKE_SESSION}`.
- Load process runs for `${SMOKE_SESSION}`.
- Preview or queue semantic-edge candidates for the smoke node.

## Completion Criteria

The smoke passes when:

- ingestion creates a document/tree/node set for the run-specific smoke source;
- duplicate or failed-path behavior is not regressed by the run;
- direct ask saves an exchange and readable activity log;
- follow-up ask can resolve `this document` from active document state;
- agentic ask returns JSON with planner/tool trace data;
- profile status and semantic candidate surfaces respond without errors;
- generated-output review surfaces respond without errors;
- web work mode remains readable without raw JSON;
- developer mode exposes the V1 inspection surfaces;
- full tests pass.
