# Tirzah

Tirzah is a locally operated, graph-based memory and retrieval layer for LLM interactions. It is intended to provide structured, navigable, provenance-aware context instead of brute-force document loading.

Repository description:

> Local-first graph memory and retrieval layer for LLM interactions, backed by MongoDB and usable from a CLI or web interface.

Suggested GitHub topics: `llm`, `memory`, `retrieval`, `knowledge-graph`, `mongodb`, `ollama`, `local-first`, `fastapi`, `python`.

## Install

The easiest supported path is Docker Compose on WSL/Linux:

```bash
docker compose build
docker compose run --rm app tirzah init --docker
docker compose up
```

Open `http://127.0.0.1:8765/`. See `docs/install.md` for Docker, Python developer installs, and runtime configuration choices.

## Routing via Hoglah (queue daemon)

Optionally route **answers and embeddings** through [Hoglah](https://github.com/gellsmore-svg/hoglah), a local-first job queue, so every model call is serialized through one durable queue and survives restarts:

```bash
pip install "tirzah[hoglah]"
```

Set `runtime.answer_adapter` and/or `runtime.embedding_adapter` to `hoglah`, then run a **separate** worker daemon pointed at the same queue + output folder:

```bash
HOGLAH_OUTPUT_DIR=data/hoglah/outbox \
  hoglah run --real --db data/hoglah/jobs.sqlite3 \
  --ollama-host http://<host>:11434 -c 1
```

Tirzah becomes a pure submitter (no in-process worker): it enqueues each call and gets the result by polling the output folder (`hoglah_delivery: poll`) or via an HTTP callback to a tiny receiver it runs (`hoglah_delivery: callback`, with poll as fallback). The `hoglah` embedding adapter is permitted for memory operations without `allow_http_ingestion_adapters` — Tirzah does only local IPC; the daemon makes the Ollama HTTP call.

## Source Documents

- `LLM_Memory_Architecture_Requirements_v0.3.md`
- `Mnemosyne_Technical_Design_v0.1.md` historical source document

## Project Knowledge Repo

- `docs/project-brief.md`
- `docs/source-documents.md`
- `docs/requirements-index.md`
- `docs/architecture-decisions.md`
- `docs/build-roadmap.md`
- `docs/open-questions.md`
- `docs/improvements-and-enhancements.md`
- `docs/repo-plan.md`
- `docs/agentic-retrieval-process.md`
- `docs/requirements-design-addendum.md`
- `docs/code-module-boundaries.md`

## Current Status

The domain is in early scaffold mode. The imported requirements and design documents have been reviewed into a compact repo of project information. MongoDB 8.0.23 is installed locally in WSL and verified running. The first CLI commands are available:

V1 is scoped as the local memory workbench release in `docs/build-roadmap.md`. Release readiness is tracked in `docs/v1-readiness-checklist.md`.

```bash
.venv/bin/tirzah db-ping
.venv/bin/tirzah ingest-one LLM_Memory_Architecture_Requirements_v0.3.md
.venv/bin/tirzah ingest-one docs/project-brief.md --label tirzah_domain
.venv/bin/tirzah ingest-folder docs --label tirzah_domain --label project_docs
.venv/bin/tirzah backfill-source-metadata
.venv/bin/tirzah enqueue-inbox
.venv/bin/tirzah process-next
.venv/bin/tirzah process-inbox
.venv/bin/tirzah queue-status
.venv/bin/tirzah queue-recent --limit 5
.venv/bin/tirzah labels
.venv/bin/tirzah sessions
.venv/bin/tirzah active-documents --session-id default
.venv/bin/tirzah create-session --title "Design review"
.venv/bin/tirzah backfill-schema-metadata
.venv/bin/tirzah show-tree <document_id>
.venv/bin/tirzah list-docs --limit 5
.venv/bin/tirzah show-doc <document_id>
.venv/bin/tirzah rebuild-document <document_id>
.venv/bin/tirzah rebuild-by-label --label tirzah_domain
.venv/bin/tirzah search-nodes --query hierarchy --label source_chunk
.venv/bin/tirzah search-nodes --document-id <document_id> --created-after 2026-05-17T14:50:00
.venv/bin/tirzah node-context <node_id>
.venv/bin/tirzah graph-edges <node_id>
.venv/bin/tirzah expand-proximity <node_id> --format text
.venv/bin/tirzah expand-graph-paths <node_id>
.venv/bin/tirzah compile-context <node_id> --ancestor-depth 2 --sibling-window 1 --child-depth 1
.venv/bin/tirzah render-context <node_id> --char-budget 4000
.venv/bin/tirzah render-context <node_id> --char-budget 900 --json
.venv/bin/tirzah build-prompt <node_id> --query "What should I know?" --token-budget 2000
.venv/bin/tirzah build-prompt <node_id> --query "What should I know?" --text
.venv/bin/tirzah ask "What should I know?" --node-id <node_id>
.venv/bin/tirzah ask "What should I know?" --node-id <node_id> --adapter ollama_cli
.venv/bin/tirzah ask "What should I know?" --node-id <node_id> --adapter ollama_cli --model gemma3:1b
.venv/bin/tirzah ask "What should I know?" --retrieval-mode agentic --model gemma3:1b
.venv/bin/tirzah chat --node-id <node_id>
.venv/bin/tirzah chat --node-id <node_id> --adapter ollama_cli
.venv/bin/tirzah chat --node-id <node_id> --adapter ollama_cli --model gemma3:1b
.venv/bin/tirzah history --limit 5
.venv/bin/tirzah profile-backfill-jobs --limit 5
.venv/bin/tirzah queue-profile-backfill --label tirzah_domain --limit 100
.venv/bin/tirzah process-profile-backfill --max-batches 1
.venv/bin/tirzah semantic-edge-candidates --format text
.venv/bin/tirzah review-semantic-edge-candidate <candidate_id> --action accept
.venv/bin/tirzah process-runs --limit 5
```

The preferred CLI is now `tirzah`. The old `mnemosyne` command remains as a compatibility entry point during the rename transition.

## Current Findings

Stage 1 now has enough working data to test retrieval behavior against both project memory and a larger imported corpus.

Agentic mode is useful because it lets the first model call decide which Tirzah retrieval tools to use before the answer call. The current weak point is still planner/search discipline: the planner can choose broad or lossy search text, so Tirzah now preserves the initiating prompt as ranking context, expands fallback candidate pools, and demotes generic document root matches. A later stricter JSON planner mode would reduce malformed planner output and make tool calls easier to validate.

A public-domain Project Gutenberg memory corpus has been imported as working data: `Memory: How to Develop, Train, and Use It` by William Walker Atkinson. It is labeled `external_corpus`, `public_domain`, and `memory_reference`. Source text is preserved as ingested, including Project Gutenberg front/back matter. It currently contains 357 nodes.

The local AMS domain has been imported as working data. The import found 1,885 Markdown/text paths, inserted 1,868 unique AMS documents into MongoDB, and rejected duplicate paths by SHA-256 checksum. AMS nodes are labeled `ams_domain`, `imported_domain`, and `research_corpus`. Source-derived tree shape is preserved, including heading-only sections. Mongo currently has 175,687 AMS-labeled nodes.

## Immediate Next Step

Continue the Stage 1 interaction slice by making Gemma's Mongo context-gathering loop more flexible and iterative while preserving source content. Proposed changes that alter source preservation, retrieval authority, or agent autonomy should be agreed before implementation.

Duplicate ingestion is rejected by SHA-256 checksum. Accepted files are copied into `data/archive/`, processed inbox requests are moved to `data/staging/processed/`, duplicate inbox requests are moved to `data/dead_letter/duplicate/`, and label meanings are seeded into MongoDB in `label_definitions`.

Worker failures retry up to `queue.max_attempts` and then move the request file to `data/dead_letter/failed/`.

Document, tree, and node records carry `schema_version: 1`. Nodes carry `endorsement_label` and provenance fields for source path, source checksum, archive path, and adapter.

New node records also carry scaffold fields for `summary`, `relations`, `proximity`, `usage_score`, and `continuity_critical`. These fields are present so the requirement-backed graph traversal, scoring, and continuity model has an explicit schema target, although full Gemma relationship/proximity generation is not implemented yet.

The deterministic mock adapter now creates hierarchical trees: `source_root`, `source_section`, and `source_chunk`.

Existing documents can be rebuilt from their archived source with `rebuild-document <document_id>`. The rebuild writes a new ingestion epoch and marks earlier trees/nodes as `superseded` rather than deleting them. The old `--force-replace` flag is still accepted as a deprecated compatibility option.

Groups of existing documents can be rebuilt by node label with `rebuild-by-label --label <label>`. Rebuilds preserve non-structural labels such as `ams_domain`, `external_corpus`, and `memory_reference`. Richer version comparison, rollback tooling, and garbage collection remain future work.

The first retrieval commands are available for listing documents, inspecting document metadata, showing tree nodes, and searching nodes by text, label, and endorsement label.

Search uses temporary lexical ordering, with requirement-backed provenance preference for explicitly and implicitly endorsed nodes. This is a tool-side ordering hint only; it does not replace the intended Gemma memory-agent selection and traversal loop.

Node search also supports document scoping and created-at bounds. `node-context` returns the selected node with document metadata, parent, and children.

`compile-context` returns a role-tagged context record set with focus, ancestors, nearby siblings, and descendants.

`render-context` produces Markdown context for model input and can return JSON metadata showing which records were included or skipped under the character budget.

Compiled context now renders the full stored node text, subject to the context budget, while search responses continue to expose compact previews.

`build-prompt` wraps rendered context with the user query, system instruction, and token-budget metadata. Token estimates use a simple four-characters-per-token approximation until a tokenizer is wired in.

`ask` and `chat` are now the first interactive surfaces. They use the retrieval pipeline, the local Ollama CLI answer adapter by default, and persist exchanges in MongoDB. Sessions can be created and listed from the CLI, and each saved exchange updates its session metadata. The mock answer adapter remains available for deterministic tests and offline diagnostics with `--adapter mock`.

For a real local model call, use the default adapter or pass `--adapter ollama_cli` explicitly. Use `--model <name>` to override the configured Ollama model for that request. The current default model is `gemma3:1b` via the Windows Ollama executable configured in `config.example.yaml`. Ollama CLI prompts are sent through stdin, run with word wrapping disabled, and are bounded by `runtime.ollama_timeout_seconds`.

Text similarity profiles default to the deterministic `mock` adapter in committed config so tests and first runs are reproducible. For the current local model-backed profile path, install the optional profile dependencies and configure the committed helper:

```bash
.venv/bin/pip install -e '.[profiles]'
```

Set `runtime.embedding_adapter: local_command`, `runtime.embedding_model: BAAI/bge-small-en-v1.5`, `runtime.embedding_dimensions: 384`, `runtime.profile_command: [tirzah-profile-helper, --worker]`, and `runtime.profile_command_mode: worker`. `tirzah init --runtime local_command` writes those defaults for new installs. In worker mode, the helper reads one `{"model": "...", "text": "..."}` request per stdin line and returns one `{"vector": [...]}` response per stdout line, keeping the model loaded across a batch. HTTP-backed profile adapters are retained only for temporary diagnostics and are blocked by default for ingestion and retrieval memory operations. Verify the selected adapter before ingestion:

```bash
.venv/bin/tirzah embedding-smoke "Taj Mahal test"
.venv/bin/tirzah embedding-smoke "Taj Mahal test" --adapter local_command --model BAAI/bge-small-en-v1.5
```

Existing active nodes can be given text similarity profiles in bounded batches without rebuilding documents:

```bash
.venv/bin/tirzah backfill-profiles --limit 100
.venv/bin/tirzah backfill-profiles --label ams_domain --limit 100
.venv/bin/tirzah backfill-profiles --document-id <document_id> --force --limit 20
.venv/bin/tirzah queue-profile-backfill --label ams_domain --limit 100
.venv/bin/tirzah process-profile-backfill --max-batches 5
.venv/bin/tirzah profile-backfill-jobs --status pending
```

`backfill-profiles` performs one immediate bounded batch. `queue-profile-backfill` creates a persistent resumable job, and `process-profile-backfill --max-batches <n>` advances the next queued job by up to `n` bounded batches while preserving cursor state, activity logs, and blocked/completed status. The older `embedding` command names remain available as compatibility aliases. The web UI exposes the same job-batch control with a smaller synchronous-request cap.

Profile candidate previews are bounded by a separate scan cap. The default scan cap is quality-first at 1000 candidates, and the CLI/API/UI can override it up to 10000 when a wider diagnostic pass is needed:

```bash
.venv/bin/tirzah profile-semantic-candidates <node_id> --candidate-scan-limit 5000
.venv/bin/tirzah enqueue-profile-semantic-batch --label ams_domain --dry-run --format text
```

For the first memory-agent flow, pass `--retrieval-mode agentic` or choose `agentic` in the web UI. In this mode Tirzah calls the configured memory-agent model iteratively, feeding prior tool results back into the agent until it stops or reaches `retrieval.memory_agent_max_iterations`. The current allowed read-only tools are `search_nodes`, `compile_context`, and `list_documents`. The final answer call is separate and uses the final answer adapter/model. This is still a scaffold: it does not yet implement semantic graph traversal, source fallback, or the full compiled context corpus schema.

## Web UI

Run:

```bash
.venv/bin/uvicorn tirzah.web.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

The default Ask workspace is work-first: create/select a session, enter a prompt, choose an Ollama model per request, press Ask, read the response, and inspect the plain activity log. This is intended to work as a normal local LLM wrapper client rather than a developer console.

Use the Developer toggle, or open `http://127.0.0.1:8765/?developer=1`, to reveal Browse/Ingestion tabs, focus-node override, adapter selection, retrieval-mode override, raw prompt/trace output, technical JSON reports, queue/status controls, semantic-edge review, and ingestion operations.

If no additional Mongo node is retrieved and no focus node is selected, Tirzah still sends the submitted prompt context to the selected answer adapter. The readable activity log remains visible in work mode. Developer mode adds the raw console trace showing ordered step input/output data for prompt intake, planner calls when enabled, tool execution, retrieval/context compilation, and answer adapter execution.

## Restart

Use `.restart.md` for resumable project state.
