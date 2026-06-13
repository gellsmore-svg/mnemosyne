# Code Module Boundaries

Date: 2026-06-08

Status: active design note. Tirzah remains one repository for now. The code should be organized so these boundaries are clear enough to split into separate packages later if they stabilize.

## Boundary Rule

Keep one repo until the interfaces stop moving. Inside the repo, organize around stable contracts rather than implementation details.

The most important rule is that MongoDB is not the architecture. MongoDB is the current persistence implementation behind the memory store contract.

Prefer this:

```text
Ingestion -> Memory Store API -> MongoDB
Retrieval -> Memory Store API -> MongoDB
Runtime   -> Memory Store API -> MongoDB
```

Avoid this:

```text
UI        -> MongoDB
Prompt    -> MongoDB
Retriever -> MongoDB
Ingestion -> MongoDB
```

The near-term compatibility facade is `src/tirzah/db/memory_store.py`. Existing code still receives a Mongo database object in many places, but new retrieval/runtime/prompt code should depend on the `MemoryStore` API instead of knowing collection names or document shape.

Inside the repo, keep modules small and directional:

```text
UI / CLI
  -> Request / Runtime
  -> Retrieval / Ingestion / Process / Prompt Pipeline
  -> Memory Store
  -> Repository / Database Implementation
  -> Models / Config
```

Lower layers should not import web UI code. Storage code should not call LLM adapters directly except through explicit ingestion/runtime functions that already own the activity log.

## Target Product Modules

### UI Adapter

Owns interaction with external users or systems: CLI, web UI, API endpoints, agent interfaces, desktop UI, or mobile UI.

Rules:

- no retrieval logic;
- no schema awareness;
- no prompt construction.

Input: user request. Output: structured request envelope.

### Request Interpreter

Owns conversion from a user request into machine-operable intent.

Expected output fields:

- `task_type`;
- `entities`;
- `required_evidence`;
- `desired_answer_shape`;
- `confidence_threshold`;
- `constraints`.

### Retrieval Orchestrator

Owns recursive retrieval and context assembly. It may generate queries, choose retrieval strategies, expand context, evaluate confidence, and decide whether another retrieval pass is required.

Inputs: structured request, `MemoryStore`, and approved external/local tools.

Output: context package.

### Prompt Packager

Owns final prompt assembly for the answer model. It separates user request, retrieval context, prompt engineering, source metadata, output format, and model-specific formatting.

Output fields should converge on:

- `system`;
- `context`;
- `constraints`;
- `user`;
- `metadata`.

### Answer Engine

Owns answer-model execution, retry/repair, and confidence validation.

Inputs: prompt package.

Output: LLM response plus diagnostics.

### Ingestion Pipeline

Owns source-to-memory conversion:

```text
Load -> Clean -> Chunk -> Enrich -> Profile -> Persist
```

Components:

- document loader;
- semantic/structural chunker;
- REM or enrichment processor;
- text similarity profile generator;
- persistence writer.

Ingestion produces memory. Retrieval interrogates memory. Shared data shape does not imply shared ownership.

### Memory Store / Persistence Layer

Owns storage, schema, query optimization, versioning, and persistence rules.

Current home:

- `src/tirzah/db/memory_store.py`;
- `src/tirzah/db/repositories.py`;
- `src/tirzah/db/indexes.py`;
- `src/tirzah/db/queue.py`;
- `src/tirzah/db/governance.py`.

Public API should converge toward names like:

- `save_chunk`;
- `get_chunks`;
- `search_semantic`;
- `search_by_entity`;
- `get_document_context`;
- `update_chunk_metadata`;
- `record_retrieval_trace`.

Forbidden outside this layer:

- direct collection queries;
- collection-name assumptions;
- Mongo schema assumptions;
- persistence write rules.

## Hook Policy

Hooks should live at architectural transitions, not everywhere.

Observer hooks are safe for logging, metrics, evaluation, and telemetry. They cannot mutate execution.

Mutation hooks are powerful and should be rare. They may modify prompts, retrieval strategy, confidence thresholds, or context only where an explicit module contract permits it.

Approved hook points:

- `before_request_interpretation`;
- `after_request_interpretation`;
- `before_retrieval_iteration`;
- `after_retrieval_iteration`;
- `before_confidence_check`;
- `after_confidence_check`;
- `before_prompt_packaging`;
- `after_prompt_packaging`;
- `before_answer_generation`;
- `after_answer_generation`;
- `before_ingestion_chunking`;
- `after_ingestion_chunking`;
- `before_rem_enrichment`;
- `after_rem_enrichment`;
- `before_persist`;
- `after_persist`.

## Current Modules

### Tirzah Core

Current home:

- `src/tirzah/db`
- `src/tirzah/models`
- core parts of `src/tirzah/retrieval`

Owns:

- documents, trees, nodes, and relationships;
- source preservation and provenance;
- text similarity profile storage;
- semantic-edge candidate review state;
- trust and temporal scoring data;
- repository rebuild and supersession state.

Mongo schema ownership belongs here and in the Memory Store / Persistence Layer. Retrieval, prompt packaging, and UI code should treat schema as hidden behind the memory store contract.

### Tirzah Runtime

Current home:

- `src/tirzah/sessions`
- `src/tirzah/adapters`
- `src/tirzah/domains`
- runtime-facing parts of `src/tirzah/retrieval`

Owns:

- prompt cycles;
- LLM adapter calls;
- memory-agent tool interface;
- context package construction;
- activity reports;
- project and conversation domain resolution;
- last prompt iteration records;
- process invocation once process objects are implemented.

Future split:

- `src/tirzah/prompt_pipeline`

The prompt pipeline should become the dedicated home for the local steps between user submission and answer-model handoff:

- prompt intake;
- intent classification;
- process selection/proposal;
- project/conversation domain resolution;
- repository-memory use decision;
- memory-agent/controller orchestration;
- context package assembly;
- answer-model handoff preparation;
- last prompt iteration persistence.

This boundary exists so Tirzah can behave as a deliberate LLM wrapper/client rather than passing prompts straight through to a target model.

Proposed submodules inside this future package:

- `request_interpreter`;
- `retrieval_orchestrator`;
- `prompt_packager`;
- `answer_engine`;
- `hooks`.

### Tirzah Ingestion

Current home:

- `src/tirzah/ingestion`
- ingestion functions in `src/tirzah/db/repositories.py`

Owns:

- source reading;
- source-date analysis;
- queue processing;
- source archiving;
- document/tree/node construction;
- text similarity profile backfill;
- future LLM-assisted ingestion.

Future cleanup:

- move ingestion-specific write orchestration out of the general repository module once the ingestion adapter is stronger;
- route ingestion persistence through `MemoryStore` instead of direct collection writes once the write contract is stable.

### Tirzah Interface

Current home:

- `src/tirzah/web`
- `src/tirzah/cli.py`

Owns:

- local web UI;
- CLI commands;
- work-mode/developer-mode presentation;
- operator controls.

Interface code should call runtime/ingestion/retrieval APIs. It should not contain memory rules that belong in core modules.

### Tirzah Corpus Tools

Current home:

- `tools`
- source staging folders under `data`

Owns:

- local helper commands;
- source preparation;
- import staging;
- future linked-domain helpers such as Mahalath import.

### Tirzah Integrations

Current home:

- not yet separated.

Expected future home:

- `src/tirzah/integrations`

Owns:

- Mahalath linked-domain integration;
- web content staging;
- voice transcript import;
- coding-agent support;
- CLI agency helpers.

## Split Triggers

Only split into separate repositories or packages when one of these becomes true:

- the interface has a stable public API used outside the repo;
- the module needs different release timing;
- the module has substantially different dependencies;
- the module can be tested independently without importing most of Tirzah;
- keeping it in the same package causes real development friction.

## Near-Term Refactor Order

1. Complete the package rename and compatibility path.
2. Add project-domain and conversation-domain data fields. Initial exchange-level fields and domain registries are implemented.
3. Add a `src/tirzah/prompt_pipeline` module and move prompt-processing orchestration toward it.
4. Add last prompt iteration records, initially in the prompt pipeline/runtime boundary.
5. Add process objects under a new `src/tirzah/processes` module.
6. Add linked-domain import support under `src/tirzah/integrations`.
7. Move retrieval reads from raw Mongo collection calls to `MemoryStore`.
8. Move ingestion write orchestration out of `db/repositories.py` only when LLM-assisted ingestion makes that boundary necessary.
