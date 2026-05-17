# Mnemosyne

Mnemosyne is a locally operated, graph-based memory and retrieval layer for LLM interactions. It is intended to provide structured, navigable, provenance-aware context instead of brute-force document loading.

Repository description:

> Local-first graph memory and retrieval layer for LLM interactions, backed by MongoDB and usable from a CLI or web interface.

Suggested GitHub topics: `llm`, `memory`, `retrieval`, `knowledge-graph`, `mongodb`, `ollama`, `local-first`, `fastapi`, `python`.

## Source Documents

- `LLM_Memory_Architecture_Requirements_v0.3.md`
- `Mnemosyne_Technical_Design_v0.1.md`

## Project Knowledge Repo

- `docs/project-brief.md`
- `docs/source-documents.md`
- `docs/requirements-index.md`
- `docs/architecture-decisions.md`
- `docs/build-roadmap.md`
- `docs/open-questions.md`
- `docs/repo-plan.md`

## Current Status

The domain is in early scaffold mode. The imported requirements and design documents have been reviewed into a compact repo of project information. MongoDB 8.0.23 is installed locally in WSL and verified running. The first CLI commands are available:

```bash
.venv/bin/mnemosyne db-ping
.venv/bin/mnemosyne ingest-one LLM_Memory_Architecture_Requirements_v0.3.md
.venv/bin/mnemosyne backfill-source-metadata
.venv/bin/mnemosyne enqueue-inbox
.venv/bin/mnemosyne process-next
.venv/bin/mnemosyne process-inbox
.venv/bin/mnemosyne queue-status
.venv/bin/mnemosyne queue-recent --limit 5
.venv/bin/mnemosyne labels
.venv/bin/mnemosyne sessions
.venv/bin/mnemosyne create-session --title "Design review"
.venv/bin/mnemosyne backfill-schema-metadata
.venv/bin/mnemosyne show-tree <document_id>
.venv/bin/mnemosyne list-docs --limit 5
.venv/bin/mnemosyne show-doc <document_id>
.venv/bin/mnemosyne search-nodes --query hierarchy --label source_chunk
.venv/bin/mnemosyne search-nodes --document-id <document_id> --created-after 2026-05-17T14:50:00
.venv/bin/mnemosyne node-context <node_id>
.venv/bin/mnemosyne compile-context <node_id> --ancestor-depth 2 --sibling-window 1 --child-depth 1
.venv/bin/mnemosyne render-context <node_id> --char-budget 4000
.venv/bin/mnemosyne render-context <node_id> --char-budget 900 --json
.venv/bin/mnemosyne build-prompt <node_id> --query "What should I know?" --token-budget 2000
.venv/bin/mnemosyne build-prompt <node_id> --query "What should I know?" --text
.venv/bin/mnemosyne ask "What should I know?" --node-id <node_id>
.venv/bin/mnemosyne ask "What should I know?" --node-id <node_id> --adapter ollama_cli
.venv/bin/mnemosyne ask "What should I know?" --node-id <node_id> --adapter ollama_cli --model gemma3:1b
.venv/bin/mnemosyne chat --node-id <node_id>
.venv/bin/mnemosyne chat --node-id <node_id> --adapter ollama_cli
.venv/bin/mnemosyne chat --node-id <node_id> --adapter ollama_cli --model gemma3:1b
.venv/bin/mnemosyne history --limit 5
```

## Immediate Next Step

Continue the Stage 1 ingestion slice with a Mongo-backed queue, folder worker, archive/dead-letter movement, duplicate handling, and endorsement labels as MongoDB tree/node metadata.

Duplicate ingestion is rejected by SHA-256 checksum. Accepted files are copied into `data/archive/`, processed inbox requests are moved to `data/staging/processed/`, duplicate inbox requests are moved to `data/dead_letter/duplicate/`, and label meanings are seeded into MongoDB in `label_definitions`.

Worker failures retry up to `queue.max_attempts` and then move the request file to `data/dead_letter/failed/`.

Document, tree, and node records carry `schema_version: 1`. Nodes carry `endorsement_label` and provenance fields for source path, source checksum, archive path, and adapter.

The deterministic mock adapter now creates hierarchical trees: `source_root`, `source_section`, and `source_chunk`.

The first retrieval commands are available for listing documents, inspecting document metadata, showing tree nodes, and searching nodes by text, label, and endorsement label.

Node search also supports document scoping and created-at bounds. `node-context` returns the selected node with document metadata, parent, and children.

`compile-context` returns a role-tagged context record set with focus, ancestors, nearby siblings, and descendants.

`render-context` produces Markdown context for model input and can return JSON metadata showing which records were included or skipped under the character budget.

`build-prompt` wraps rendered context with the user query, system instruction, and token-budget metadata. Token estimates use a simple four-characters-per-token approximation until a tokenizer is wired in.

`ask` and `chat` are now the first interactive surfaces. They use the retrieval pipeline, a mock answer adapter, and persist exchanges in MongoDB. Sessions can be created and listed from the CLI, and each saved exchange updates its session metadata.

For a real local model call, pass `--adapter ollama_cli`. Use `--model <name>` to override the configured Ollama model for that request. The current default model is `gemma3:1b` via the Windows Ollama executable configured in `config.example.yaml`.

## Web UI

Run:

```bash
.venv/bin/uvicorn mnemosyne.web.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

The web UI can create/select sessions, search nodes, focus a node, ask questions with `mock` or `ollama_cli`, choose an Ollama model per request, show recent exchanges, show queue status, process `data/ingest/`, and list recent jobs. To call a local LLM from the browser, set the adapter to `ollama_cli`, choose a model such as `gemma3:1b`, optionally focus a node, and press Ask.

If no Mongo node matches the prompt and no focus node is selected, Mnemosyne still sends the submitted prompt to the selected answer adapter. The answer panel includes a compact run log showing whether retrieved context was used.

## Restart

Use `.restart.md` for resumable project state.
