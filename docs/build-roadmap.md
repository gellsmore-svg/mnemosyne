# Build Roadmap

Last updated: 2026-05-17

## Stage 0 — Project Scaffold

Goal: create a runnable Python project skeleton with config, tests, and local folders.

Expected outputs:

- `pyproject.toml`
- `src/mnemosyne/`
- `tests/`
- `config.example.yaml`
- CLI/dev command entrypoint for ingestion and database inspection;
- verified local MongoDB connection against WSL MongoDB 8.0.23;
- local data folders ignored by git:
  - `data/ingest/`
  - `data/archive/`
  - `data/dead_letter/`
  - `data/staging/`

## Stage 1 — Phase 1 Ingestion

Goal: prove single-document ingestion.

Minimum build:

- direct markdown/plaintext parser;
- MongoDB connection layer using the real local database;
- queue collection and queue adapter;
- inbox enqueue command;
- single-job processor command;
- inbox processor command;
- queue status/recent inspection commands;
- staging collection;
- model adapter interface;
- stub/mock adapter for tests;
- Gemma adapter placeholder or minimal local implementation, without requiring `llama-cpp-python` in the first pass;
- transaction-like commit boundary;
- retry/error/dead-letter handling;
- document/tree/node schema writes, including endorsement labels/provenance fields where applicable;
- `schema_version: 1` on document, tree, and node records;
- explicit node `endorsement_label`;
- deterministic hierarchy with root, section, and paragraph chunk nodes;
- Mongo `parent_id` links plus adapter-facing `node_key` / `parent_key`;
- provenance fields for source path, checksum, archive path, endorsement label, and adapter;
- source SHA-256 checksum calculation;
- duplicate checksum rejection with requestor notification;
- source archive copy for accepted files;
- MongoDB label definition lookup collection;
- embeddings optional behind interface, mockable at first.

Definition of done:

- dropping a `.md` file into the ingestion folder creates a queue job;
- worker stages the file and computes a checksum;
- duplicate checksum input is rejected without creating a second document;
- accepted source file is copied into the archive;
- accepted inbox file is moved to `data/staging/processed/`;
- duplicate inbox file is moved to `data/dead_letter/duplicate/`;
- worker failure retries are bounded by `queue.max_attempts`;
- final worker failures are moved to `data/dead_letter/failed/`;
- label definitions are present in MongoDB with key, scope, and description;
- live records can be inspected/backfilled for schema metadata;
- hierarchy can be inspected with `show-tree`;
- adapter returns structured ingestion JSON;
- document, tree, and node records are written;
- file moves to archive;
- malformed adapter output or parse failure goes through retry/dead-letter path;
- tests cover success, parse failure, processing failure, and database-write failure path as far as practical.
- a CLI command can ingest one file and print the created document/tree/node IDs.

## Stage 2 — Local Web Interface

Goal: usable testing surface.

Current early web surface:

- FastAPI app;
- static HTML/CSS/JS UI;
- health, document, search, ask, and history APIs;
- session create/list APIs;
- queue status, recent job, and process-inbox APIs;
- node focus and adapter selection controls;
- session selector/create controls;
- active document API for session-scoped document references;
- direct/agentic retrieval mode selector;
- operator controls for inbox processing.

Minimum build:

- FastAPI backend;
- one static HTML/CSS/JS frontend;
- session creation/listing;
- tabbed sessions;
- prompt/response loop;
- streaming-compatible API shape, even if initial model response is mocked.

Application pressure:

- keep the web UI as one client of the memory engine, not the only interface;
- preserve API shapes that can also serve CLI agents, web importers, voice transcript adapters, and future FOSS tool integrations.

## Stage 3 — Retrieval And Context Compilation

Goal: retrieve from graph and compile context.

Current early read surface:

- list documents;
- show document metadata and counts;
- show a document tree;
- search nodes by text, label, endorsement label, document ID, and created-at bounds;
- retrieve node context with document metadata, parent, and children.
- compile a role-tagged context record set from a focus node, including ancestors, nearby siblings, and descendants.
- CLI graph inspection commands for single-hop edges, one-hop proximity expansion, and bounded multi-hop path expansion.
- CLI graph status command reporting total edge count plus relation/provenance breakdowns.
- CLI and memory-agent semantic-candidate diagnostics for read-only label-overlap candidate inspection before writing inferred semantic edges.
- CLI semantic-edge candidate queue commands can enqueue pending review rows from semantic candidates and list them for batched operator review.
- CLI semantic-edge candidate review can accept queued candidates into reviewed graph edges or reject them with reviewer/note metadata.
- CLI reviewed semantic-edge promotion can turn two known nodes into a typed directed graph edge with reviewer/note provenance, bounded weight/confidence, duplicate protection, and shared-label evidence.
- render compiled context to Markdown with include/skip metadata under a character budget.
- build a prompt envelope with system instruction, user query, rendered context, and estimated token budget metadata.
- ask/chat commands using the retrieval pipeline and local Ollama CLI answer adapter by default;
- optional Ollama CLI answer adapter for real local model calls;
- saved exchange records in MongoDB.
- saved exchange usage-summary updates are written before output-ingestion queue linking, keeping `scored_node_count` aligned even if output queueing fails.
- active document records are updated from answer `used_node_ids`, preserving session/document/source/node references for later continuity and endorsement work.
- active document records accumulate referenced node IDs and labels across repeated session references.
- active document serialization returns stable sorted node IDs and labels for deterministic UI/API traces.
- saved exchanges increment `usage_score` and `last_used_at` on the used non-rejected nodes, giving retrieval feedback a first persisted signal.
- direct retrieval applies a capped usage-score boost and last-used tie-breaker, while keeping rejection/provenance penalties dominant; serialized nodes expose raw `usage_score` plus capped `usage_score_bonus`.
- direct retrieval uses active document scoping for reference-shaped session prompts such as "this document" or "previous source", checking all active documents for topical matches before falling back to the first active document root/default node.
- direct retrieval can read a bounded excerpt from an active document's local archive/source path as a source fallback when reference-shaped prompts cannot produce a Mongo focus node.
- saved answer text is queued as pending LLM-output ingestion work, linked back to the originating exchange and session.
- pending LLM-output ingestion jobs can be processed into unreviewed graph documents with generated-output labels and exchange/session provenance.
- generated-output nodes can be explicitly reviewed through CLI/API and marked `unreviewed`, `implicit_endorsed`, `explicit_endorsed`, or `rejected`, updating node provenance and review history.
- first iterative memory-agent retrieval loop: memory-agent model emits bounded JSON tool calls, Mnemosyne executes allowed retrieval tools, feeds observations back to the memory-agent, and only then calls the final answer model.
- memory-agent prompts include the current session ID and compact active document summaries; the read-only tool surface includes active-document listing plus exact document, document-tree, and node-context lookup tools.
- memory-agent graph-edge lookup can inspect bounded incoming/outgoing typed relations for a known node ID.
- memory-agent one-hop proximity expansion can rank adjacent graph nodes by edge weight/confidence.
- memory-agent bounded graph-path expansion can rank multi-hop path targets by multiplied edge weight/confidence and compile context for the top targets.
- active document titles, source paths, and labels feed near-match query guidance and fallback search vocabulary for the current session.
- structured process trace for prompt intake, planner call, tool execution, retrieval/context compilation, and answer call.
- agentic answer prompts persist a first structured `context_document` alongside the rendered Markdown context; this is a scaffold toward the full context schema.
- memory-agent search fallback and lexical ranking, with full console tool output but top-ranked context packaged for the answer model.
- first typed `graph_edges` collection writes exist for ingestion relation hints that point to known node keys.
- structural `contains` graph edges can be backfilled from existing parent/child node links, giving graph traversal a source-faithful document-tree substrate before semantic relation extraction exists.

Known gaps after reconciliation:

- current ingestion chunking is deterministic scaffold, not Gemma-driven chunking;
- graph edge writes, single-hop edge lookup, one-hop proximity expansion, bounded multi-hop path expansion, structural parent/child edge backfill, read-only semantic-candidate diagnostics, a pending semantic-edge candidate queue, candidate accept/reject review, and reviewed semantic-edge promotion are first scaffolds; automated relation extraction, richer path scoring, and semantic-map traversal are not implemented;
- destructive rebuild commands are maintenance-only and require `--force-replace`; versioned replacement remains unimplemented;
- the memory-agent loop is iterative but still limited to read-only scaffold tools;
- the compiled context corpus has only a first structured scaffold and does not yet match the full technical design schema;
- active document registry is only a first skeleton populated from used nodes, visible to the memory-agent, and used for narrow direct reference resolution/source fallback; it does not yet drive broad retrieval, endorsement, or restart state.
- output ingestion is implemented only as conservative graph insertion plus explicit review labels; natural-language endorsement detection, relation extraction, restart state node, full traversal path scoring, unused-path decay, and REM consolidation are not started.

Minimum build:

- qualitative vector/semantic query;
- quantitative lookup by document/tree/node ID;
- traversal loop;
- proximity expansion;
- cold-start fallback to active document registry;
- context document JSON;
- improve real-model prompt quality and model selection;
- web UI after CLI behavior stabilizes;
- brute-force baseline comparison harness.

Integration-facing minimum:

- stable context envelopes for external coding agents and CLI tools;
- exact lookup APIs for documents, nodes, exchanges, sessions, and active documents;
- source-ingestion hooks that allow web and voice tools to submit local files or transcripts through the normal queue.

## Stage 4 — REM Consolidation And Semantic Map

Goal: cross-document consolidation.

Minimum build:

- low-confidence edge candidate query;
- embedding pre-clustering;
- Gemma confirmation;
- cross-document edge writes;
- semantic map sense clusters;
- semantic map snapshots.

## Stage 5 — Session Continuity And Endorsement

Goal: persistent working memory.

Minimum build:

- session prompt/response chunking;
- exchanges collection;
- restart state update;
- `.restart.md` renderer;
- active document registry;
- continuity-critical flag handling;
- endorsement detection and chunk-level provenance updates.

## Stage 6 — Traversal Scoring And Feedback

Goal: learning retrieval paths.

Minimum build:

- exchange records include used nodes and traversal paths;
- scoring increments used node/path scores;
- traversed-unused paths decrement;
- recommendation mechanism for high-score paths to active documents.

## Stage 7 — Practical Integrations

Goal: expose Mnemosyne as a durable memory backend for practical tools.

Minimum build:

- URL/source import command or API that writes local source files and queues normal ingestion;
- explicit capture endpoint for notes, command results, code reviews, and design decisions;
- transcript ingestion endpoint for voice tools, with confidence and correction metadata;
- read-only memory tool surface suitable for coding agents and CLI workflows;
- FOSS tool evaluation for web capture, voice capture, local agent shells, and MCP-compatible clients.

Design note:

- see `docs/practical-applications.md` for the current application lanes and integration criteria.
