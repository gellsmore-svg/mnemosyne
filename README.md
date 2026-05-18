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
.venv/bin/mnemosyne ingest-one data/ingest/source.txt --label external_corpus --label memory_reference
.venv/bin/mnemosyne ingest-folder /home/cello/domains/AMS --label ams_domain --label imported_domain --label research_corpus
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
.venv/bin/mnemosyne rebuild-document <document_id> --force-replace
.venv/bin/mnemosyne rebuild-by-label --label ams_domain --force-replace
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
.venv/bin/mnemosyne ask "What should I know?" --retrieval-mode agentic --model gemma3:1b
.venv/bin/mnemosyne chat --node-id <node_id>
.venv/bin/mnemosyne chat --node-id <node_id> --adapter ollama_cli
.venv/bin/mnemosyne chat --node-id <node_id> --adapter ollama_cli --model gemma3:1b
.venv/bin/mnemosyne history --limit 5
```

## Current Findings

Stage 1 now has enough working data to test retrieval behavior against both project memory and a larger imported corpus.

Agentic mode is useful because it lets the first model call decide which Mnemosyne retrieval tools to use before the answer call. The current weak point is still planner/search discipline: the planner can choose broad or lossy search text, so Mnemosyne now preserves the initiating prompt as ranking context, expands fallback candidate pools, and demotes generic document root matches. A later stricter JSON planner mode would reduce malformed planner output and make tool calls easier to validate.

A public-domain Project Gutenberg memory corpus has been imported as working data: `Memory: How to Develop, Train, and Use It` by William Walker Atkinson. It is labeled `external_corpus`, `public_domain`, and `memory_reference`. Source text is preserved as ingested, including Project Gutenberg front/back matter. It currently contains 357 nodes.

The local AMS domain has been imported as working data. The import found 1,885 Markdown/text paths, inserted 1,868 unique AMS documents into MongoDB, and rejected duplicate paths by SHA-256 checksum. AMS nodes are labeled `ams_domain`, `imported_domain`, and `research_corpus`. Source-derived tree shape is preserved, including heading-only sections. Mongo currently has 175,687 AMS-labeled nodes.

## Immediate Next Step

Continue the Stage 1 interaction slice by making Gemma's Mongo context-gathering loop more flexible and iterative while preserving source content. Proposed changes that alter source preservation, retrieval authority, or agent autonomy should be agreed before implementation.

Duplicate ingestion is rejected by SHA-256 checksum. Accepted files are copied into `data/archive/`, processed inbox requests are moved to `data/staging/processed/`, duplicate inbox requests are moved to `data/dead_letter/duplicate/`, and label meanings are seeded into MongoDB in `label_definitions`.

Worker failures retry up to `queue.max_attempts` and then move the request file to `data/dead_letter/failed/`.

Document, tree, and node records carry `schema_version: 1`. Nodes carry `endorsement_label` and provenance fields for source path, source checksum, archive path, and adapter.

New node records also carry scaffold fields for `summary`, `relations`, `proximity`, `usage_score`, and `continuity_critical`. These fields are present so the requirement-backed graph traversal, scoring, and continuity model has an explicit schema target, although full Gemma relationship/proximity generation is not implemented yet.

The deterministic mock adapter now creates hierarchical trees: `source_root`, `source_section`, and `source_chunk`.

Existing documents can be destructively replaced from their archived source with `rebuild-document <document_id> --force-replace`. This is a maintenance escape hatch for prototype repair work, not requirement-compliant versioned ingestion. Without `--force-replace`, the command refuses to run.

Groups of existing documents can be destructively replaced by node label with `rebuild-by-label --label <label> --force-replace`. Rebuilds preserve non-structural labels such as `ams_domain`, `external_corpus`, and `memory_reference`, but still delete and recreate trees/nodes. The requirement-backed replacement path still needs versioned trees and supersession edges.

The first retrieval commands are available for listing documents, inspecting document metadata, showing tree nodes, and searching nodes by text, label, and endorsement label.

Search uses temporary lexical ordering, with requirement-backed provenance preference for explicitly and implicitly endorsed nodes. This is a tool-side ordering hint only; it does not replace the intended Gemma memory-agent selection and traversal loop.

Node search also supports document scoping and created-at bounds. `node-context` returns the selected node with document metadata, parent, and children.

`compile-context` returns a role-tagged context record set with focus, ancestors, nearby siblings, and descendants.

`render-context` produces Markdown context for model input and can return JSON metadata showing which records were included or skipped under the character budget.

Compiled context now renders the full stored node text, subject to the context budget, while search responses continue to expose compact previews.

`build-prompt` wraps rendered context with the user query, system instruction, and token-budget metadata. Token estimates use a simple four-characters-per-token approximation until a tokenizer is wired in.

`ask` and `chat` are now the first interactive surfaces. They use the retrieval pipeline, the local Ollama CLI answer adapter by default, and persist exchanges in MongoDB. Sessions can be created and listed from the CLI, and each saved exchange updates its session metadata. The mock answer adapter remains available for deterministic tests and offline diagnostics with `--adapter mock`.

For a real local model call, use the default adapter or pass `--adapter ollama_cli` explicitly. Use `--model <name>` to override the configured Ollama model for that request. The current default model is `gemma3:1b` via the Windows Ollama executable configured in `config.example.yaml`. Ollama CLI prompts are sent through stdin, run with word wrapping disabled, and are bounded by `runtime.ollama_timeout_seconds`.

For the first memory-agent flow, pass `--retrieval-mode agentic` or choose `agentic` in the web UI. In this mode Mnemosyne calls the configured memory-agent model iteratively, feeding prior tool results back into the agent until it stops or reaches `retrieval.memory_agent_max_iterations`. The current allowed read-only tools are `search_nodes`, `compile_context`, and `list_documents`. The final answer call is separate and uses the final answer adapter/model. This is still a scaffold: it does not yet implement semantic graph traversal, source fallback, or the full compiled context corpus schema.

## Web UI

Run:

```bash
.venv/bin/uvicorn mnemosyne.web.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

The web UI can create/select sessions, search nodes, focus a node, ask questions with the default `ollama_cli` adapter or the diagnostic `mock` adapter, choose an Ollama model per request, show recent exchanges, show queue status, process `data/ingest/`, and list recent jobs. To call a local LLM from the browser, leave the adapter on default, choose a model such as `gemma3:1b`, optionally focus a node, and press Ask.

If no additional Mongo node is retrieved and no focus node is selected, Mnemosyne still sends the submitted prompt context to the selected answer adapter. The answer panel includes a console trace showing ordered step input/output data for prompt intake, planner calls when enabled, tool execution, retrieval/context compilation, and answer adapter execution.

## Restart

Use `.restart.md` for resumable project state.
