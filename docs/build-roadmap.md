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
- operator controls for inbox processing.

Minimum build:

- FastAPI backend;
- one static HTML/CSS/JS frontend;
- session creation/listing;
- tabbed sessions;
- prompt/response loop;
- streaming-compatible API shape, even if initial model response is mocked.

## Stage 3 — Retrieval And Context Compilation

Goal: retrieve from graph and compile context.

Current early read surface:

- list documents;
- show document metadata and counts;
- show a document tree;
- search nodes by text, label, endorsement label, document ID, and created-at bounds;
- retrieve node context with document metadata, parent, and children.
- compile a role-tagged context record set from a focus node, including ancestors, nearby siblings, and descendants.
- render compiled context to Markdown with include/skip metadata under a character budget.
- build a prompt envelope with system instruction, user query, rendered context, and estimated token budget metadata.
- ask/chat commands using the retrieval pipeline and a mock answer adapter;
- optional Ollama CLI answer adapter for real local model calls;
- saved exchange records in MongoDB.

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
- async scoring job increments used node/path scores;
- traversed-unused paths decrement;
- recommendation mechanism for high-score paths to active documents.
