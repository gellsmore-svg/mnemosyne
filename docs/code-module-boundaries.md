# Code Module Boundaries

Date: 2026-06-07

Status: active design note. Tirzah remains one repository for now. The code should be organized so these boundaries are clear enough to split into separate packages later if they stabilize.

## Boundary Rule

Keep one repo until the interfaces stop moving. Inside the repo, keep modules small and directional:

```text
UI / CLI
  -> Runtime
  -> Retrieval / Ingestion / Process
  -> Repository / Database
  -> Models / Config
```

Lower layers should not import web UI code. Storage code should not call LLM adapters directly except through explicit ingestion/runtime functions that already own the activity log.

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

- move ingestion-specific write orchestration out of the general repository module once the ingestion adapter is stronger.

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
3. Add last prompt iteration records under `src/tirzah/sessions`.
4. Add process objects under a new `src/tirzah/processes` module.
5. Add linked-domain import support under `src/tirzah/integrations`.
6. Move ingestion write orchestration out of `db/repositories.py` only when LLM-assisted ingestion makes that boundary necessary.
