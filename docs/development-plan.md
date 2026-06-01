# Development Plan

Date: 2026-05-30

Status: active implementation plan. This plan reconciles the current requirements around ingestion quality, real-world human testing, and human-readable transparency.

## Guiding Priorities

1. Ingestion quality comes first. Poor ingestion creates poor memory, poor retrieval, and misleading confidence.
2. Human usability must improve alongside ingestion, because the UI is the main test bench for real use.
3. Human-readable transparency is required for every major step. JSON may exist for machines, but it must not be the normal path for understanding what happened.

## Agreed Strategy

The next development phase should build an ingestion-quality spine while preserving the answer/retrieval transparency already implemented.

The system should move in this order:

1. Ingestion epoch and non-destructive rebuild foundation.
2. Chronological source-date extraction.
3. Human-readable ingestion activity logs.
4. UI support for ingestion runs, epochs, and log inspection.
5. Embedding interface and semantic candidate substrate.
6. LLM-assisted ingestion adapter.
7. Reviewed semantic relationship promotion.
8. Ranking, trust, and temporal integration.

Internet-assisted reasoning, live server-pushed streaming, and process enforcement should not be pulled forward until ingestion epochs, semantic candidates, and reviewable provenance are stronger. Those features depend on trust and graph foundations that are not ready yet.

## Slice 1: Ingestion Epoch Foundation

Goal:

- Stamp documents, trees, nodes, and node provenance with an `ingestion_epoch`.
- Allow CLI ingestion and maintenance rebuild commands to accept an explicit epoch ID.
- Generate a conservative date-based default epoch when none is provided.

Why:

- Later non-destructive rebuilds need a way to distinguish old and new derived structures.
- Chronological corpus builds need run-level provenance.
- Human ingestion logs need to say which ingestion run produced which objects.

Non-goal:

- This slice does not make rebuild non-destructive by itself. It creates the metadata surface required for that change.

## Slice 2: Non-Destructive Rebuild

Implementation status: versioned rebuild insertion is implemented; epoch comparison and explicit garbage collection remain open.

Goal:

- Replace destructive tree/node deletion with versioned insertion.
- Mark previous tree/node sets as superseded rather than deleting them.
- Preserve endorsements, usage scores, active-document references, and reviewed semantic edges.

Required behavior:

- Rebuild should create a new active tree under a new epoch.
- Previous trees/nodes should remain queryable for audit unless explicitly garbage-collected.
- Retrieval should prefer active epoch records by default.

Implemented behavior:

- `rebuild-document` and `rebuild-by-label` no longer require destructive replacement for normal operation.
- Previous document trees and nodes are marked `superseded`.
- New trees and nodes are inserted as `active` under the selected ingestion epoch.
- Normal search and document-tree views exclude superseded nodes by default.

## Slice 3: Chronological Source-Date Extraction

Implementation status: source-date extraction and persistence are implemented for direct CLI ingestion, queued ingestion, and maintenance rebuilds. CLI folder ingestion now orders sources by selected origin date, then path.

Goal:

- Determine earliest credible source origin date for each document.

Priority order:

1. Explicit date inside document content.
2. Date embedded in filename.
3. Original file creation date, when preserved by the source acquisition path.
4. Original file modification date, when preserved by the source acquisition path.

Output:

- Store date candidates and the chosen date with rationale.
- Use chronology for corpus build ordering, especially AMS / RS / RS5 collections.

Implemented behavior:

- The ingestion date analyzer records explicit content dates, filename dates, filesystem creation/change dates, and filesystem modification dates as structured candidates.
- Filesystem candidates are treated as weak fallback evidence because web uploads and copied files can reflect import time rather than authorship time.
- The selected origin date and rationale are stored in document `source` metadata.
- Retrieval document serialization exposes `origin_date` and `origin_date_source`.
- `ingest-folder` builds a chronological source plan and reports the first 20 planned sources in `source_order`.
- `ingest-folder` and the Ingestion tab preserve unreadable supported files as explicit error rows instead of failing the entire folder/listing.

## Slice 4: Human-Readable Ingestion Logs

Implementation status: initial direct and queued ingestion activity logs are implemented. Logs are attached alongside structured ingestion activity reports, web inbox processing displays the plain operator log by default, completed queue jobs retain their readable log, and the inbox browser shows origin-date ordering context. LLM-assisted ingestion logs remain open until the ingestion adapter exists.

Goal:

- Give every ingestion operation a readable activity report comparable to answer activity logs.

The log should explain:

- source detected;
- checksum and duplicate status;
- date candidates and chosen date;
- adapter used;
- chunks/nodes created;
- relationship hints detected or proposed;
- repository writes;
- failures, retries, and review requirements.

Implemented behavior:

- Direct CLI ingestion returns `activity_report` and `activity_log` for successful and duplicate-rejected files.
- Queue worker ingestion returns `activity_report` and `activity_log` for successful, rejected, retrying, failed, and missing-source jobs.
- Completed queue jobs persist their ingestion activity log inside the stored job result for later review.
- Web inbox processing aggregates per-job logs into an `Inbox Processing Activity Log` and shows that human-readable log in the Ingestion tab instead of defaulting to raw JSON.

## Slice 5: Ingestion UI Test Bench

Goal:

- Make the Ingestion tab usable for real-world corpus testing.

Required views:

- current epoch;
- recent ingestion runs;
- readable ingestion log;
- source file status;
- created document/tree/node counts;
- candidate relationships awaiting review;
- comparison between epochs when available.

Implemented behavior:

- The inbox browser shows selected origin date, origin-date source, and date-candidate count for staged files, ordered by origin date and then path.
- Recent jobs expose the persisted readable ingestion log for completed queue jobs.
- `/api/ingestion/status` and the Ingestion tab now summarize recent ingestion epochs and ingestion process runs, including the current/latest epoch by update time and dated-document coverage per epoch.

## Slice 6: Semantic Substrate

Goal:

- Add an embedding interface behind a mockable adapter boundary.
- Store vectors without immediately changing ranking behavior.
- Generate semantic candidates into the existing review queue.

Constraint:

- Semantic writes remain candidate/review based. The memory-agent should not gain autonomous write authority.

Status: Phase A implemented (mockable embedding substrate); Phase B partially implemented (stored-vector semantic candidate diagnostics and review-queue enqueueing).

Phase A implemented behavior:

- A dependency-free embedding adapter boundary lives in `mnemosyne.adapters.embedding`, with a deterministic `MockEmbeddingAdapter` and an `embedding_adapter(config)` factory selected by `runtime.embedding_adapter` / `runtime.embedding_dimensions`.
- The mock adapter derives a bounded, unit-norm vector from a SHA-256 expansion of the node text, so the same text always produces the same vector without any external model or network call.
- During direct CLI ingestion and queued worker ingestion (both via `commit_ingestion`), and during maintenance rebuilds (`rebuild_document`), each node is annotated before commit with an `embedding` field holding adapter/model name, dimensions, vector, and the source text hash.
- Ingestion activity reports/logs now surface the embedded node count, embedding model, and dimensions.
- This phase intentionally does not change search ranking, answer behavior, or memory-agent write authority.
- Stored vectors can now be read by operator commands to propose pending semantic-edge candidates, distinguished from label-overlap candidates by `candidate_source: embedding_similarity` and similarity/model/dimension metadata.

Phase A remaining work:

- Add web/API controls for triggering vector-backed candidate enqueueing from the Ingestion review tab.
- Wire a real local embedding adapter behind the same boundary.
- Only then consider any retrieval/ranking use of embeddings, kept candidate/review based.

## Slice 7: LLM-Assisted Ingestion

Goal:

- Introduce a quality-first local LLM ingestion adapter.

The adapter should produce:

- semantic chunks;
- concept/entity observations;
- relationship hints;
- sensitivity/trust notes;
- source-date observations;
- structured ingestion log entries.

Writes should remain reviewable until trust, identity, and governance controls are stronger.

## Slice 8: Ranking And Trust Integration

Goal:

- Move away from increasingly hardcoded lexical ranking.
- Consolidate ranking into a requirement-cited scoring module.
- Integrate reviewed semantic relationships, trust, temporal relevance, usage, and active-document context.

Constraint:

- Each ranking factor must be explainable in the activity report.

## Documentation Maintenance

Before or during this phase:

- Mark stale historical reports as superseded where they no longer describe current behavior.
- Close the open documentation question around the `rejected` endorsement state.
- Keep `docs/consolidated-requirements-and-design.md` as the canonical product/design entry point.
- Keep this file as the active implementation sequence.
