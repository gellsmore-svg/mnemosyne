---
conversation_id: conv-2026-06-14-tirzah-requirements-impl-review
message_id: "0001"
from: codex
to: claude
type: requirements-implementation-review
timestamp: 2026-06-14T20:15:00Z
references:
  - docs/consolidated-requirements-and-design.md
  - docs/build-roadmap.md
  - docs/v1-readiness-checklist.md
  - docs/architecture-decisions.md
  - docs/improvements-and-enhancements.md
  - README.md
status: filed-into-product-docs
filed_at: 2026-06-15
priority: high
tags:
  - tirzah
  - v1
  - requirements
  - implementation-review
  - spec-audit
---
# Tirzah Requirements/Spec to Implementation Review

**Date of review:** 2026-06-14  
**Scope:** Full audit of current codebase against canonical specs (not a recent-diff review). Primary sources: `docs/consolidated-requirements-and-design.md`, `docs/build-roadmap.md` (V1 scope/gates), `docs/v1-readiness-checklist.md`, `docs/architecture-decisions.md`, `docs/current-product-requirements-and-design.md`, `docs/lifecycle-next-phase-requirements.md`, `docs/improvements-and-enhancements.md`, `README.md`, smoke docs, and fixtures.  
**Methodology:** Read all listed spec documents + key implementation files (ingestion/*, sessions/*, db/*, retrieval/*, adapters/*, web/app.py + static, cli.py, config.py, models/, domains/, tests/ key modules) + greps for boundary/HTTP/source/provenance/activity patterns + cross-reference of every V1 gate and core principle.  
**Format for every finding:** Severity, exact File:line (or architectural note), Spec reference, Description, Evidence (spec quote + code/test behavior), Suggestion, Status.

All issues are observations only; no code was modified.

---

## Core Principles Audit (from consolidated-requirements-and-design.md:36-84)

### Issue 1 -- Severity: strength
- File: src/tirzah/ingestion/parser.py:14 (and files.py:16, worker.py:71, cli.py:175, repositories.py:200-221)
- Spec reference: consolidated-requirements-and-design.md:42 ("The system must preserve source text and provenance. It must not silently rewrite, compress, summarize, or clean source material during ingestion unless that transformed material is explicitly stored as derived content."); also build-roadmap Stage 1 and V1 must-include.
- Description: Full original source text is read once and stored verbatim in every `IngestedNode.text` (and `NodeRecord.text`) under `source_chunk` / hierarchy nodes. Checksum (SHA-256) + archive copy + provenance block (source_path, checksum, archive_path, adapter, endorsement) are attached at commit time. No silent rewriting occurs for primary content.
- Evidence: `read_text_source` does full `handle.read()`; `MockIngestionAdapter.process` passes full `text` into nodes (mock.py:46 for chunks, 79 for root uses summarize only as metadata); `commit_ingestion` + `insert_tree_nodes` write `text=node.text` directly + `Provenance` model; activity reports record "source text preserved" implicitly via counts + archive paths. Matches "Source Authority" and "Repository Refresh" model.
- Suggestion: Maintain this invariant strictly in any future LLM chunking adapter (see improvements-and-enhancements.md:1.1).
- Status: open (strength to preserve)

### Issue 2 -- Severity: strength
- File: src/tirzah/ingestion/activity.py:85 (ingestion_activity_log), sessions/activity_reports.py:21 (answer_activity_log), web/app.py (readable log defaults)
- Spec reference: consolidated-requirements-and-design.md:58-71 ("Transparency First": "The user should be able to understand what happened without reading Python, raw JSON... default surface should read like a clear application log."); also build-roadmap V1 must-include and current-product-requirements-and-design.md:25.
- Description: Both ingestion and answer flows emit a structured `activity_report` + plain-English `activity_log`. Web UI defaults to the plain log (raw JSON behind "technical detail"). CLI smoke and web smoke exercise this. Logs cover source analysis, dating, semantic counts, repo writes, dead-letter, controller decisions, tool repairs, etc.
- Evidence: `attach_ingestion_activity` + `ingestion_activity_log` produce bullet timelines; `answer_activity_log` produces "What happened:", "Context construction:", "LLM activity:", repair guidance, system functions; v1-readiness-checklist and smoke.md verify "readable activity log" passes; web developer toggle reveals JSON.
- Suggestion: Continue expanding these (e.g. per-node "why included" as proposed in improvements 7.2).
- Status: open (strength)

### Issue 3 -- Severity: strength | gap (partial)
- File: src/tirzah/adapters/embedding.py:386 (guard), sessions/interaction.py:1407 (memory-agent guard), config.py:25 (`allow_http_ingestion_adapters`), hoglah_runtime.py (pure submitter IPC), web/app.py:606 and cli embedding paths
- Spec reference: consolidated-requirements-and-design.md:81-83 ("Local Memory Interface Boundary": "HTTP must not be used for ingestion, retrieval, memory-agent tool orchestration, Python memory tools, or embedding generation for repository memory. Python remains the stateful orchestrator..."); ADR-019; build-roadmap V1 must-include.
- Description: Strong enforcement: HTTP-backed embedding adapters (`ollama_http`, etc.) raise on use for memory ops unless explicitly allowed (default False). Answer HTTP allowed only for final answer (not memory-agent). Hoglah adapters explicitly documented as local IPC (submit to SQLite + poll/callback; daemon owns Ollama HTTP). `embedding_adapter` factory and `memory_agent_runtime_config` enforce.
- Evidence: Guard in `embedding_adapter`: "Embedding adapter '...' is HTTP-backed and is not allowed..."; interaction.py blocks HTTP for memory_agent_adapter; hoglah docs in ADR + code + README; web backfill/ingest paths call the guarded adapter and surface "not_allowed" via process runs.
- Suggestion: The "operator-sanctioned" Hoglah carve-out is well-documented but adds surface area; ensure all new memory tools also route through the Python runtime validator.
- Status: open (mostly strength, minor documentation surface risk)

### Issue 4 -- Severity: gap
- File: No single line — architectural (see also consolidated-requirements-and-design.md:274 "Last Prompt Iteration Record" marked open; build-roadmap Stage 5)
- Spec reference: consolidated-requirements-and-design.md:258-274 (continuity record with submitted prompt, intent, domains, retrieved/rejected chunks, process, tool calls, final context package, answer, unresolved items; "The UI should expose the last used chunks/context records in a simple continuity panel"); build-roadmap V1 must-include "saved ask/chat exchanges, history, session selection, and active document references".
- Description: Exchanges + active_documents + session touch + domains on exchanges provide partial continuity, but the explicit "last prompt iteration record" + dedicated continuity panel (beyond active docs and history list) is not implemented. Active documents are populated from used nodes but do not yet capture full rejected chunks, controller proposal, or unresolved follow-ups as a first-class record.
- Evidence: `save_exchange` records query/answer/prompt/context_metadata/used nodes but no separate "last_iteration" collection or panel; sessions/interaction.py and active_documents.py implement the skeleton described as "first skeleton" in build-roadmap:244; CLI `active-documents` and web tab exist; improvements-and-enhancements.md:5.1 lists "Last Prompt Iteration Records and Continuity Panel" as high-priority post-V1.
- Suggestion: Implement the continuity artifact described in the spec before claiming full V1 continuity.
- Status: open

---

## V1 Scope / Gates Audit (build-roadmap.md:17-57 + v1-readiness-checklist.md)

See dedicated section at end of this document.

---

## Ingestion Pipeline

### Issue 5 -- Severity: claim-mismatch | gap
- File: src/tirzah/ingestion/worker.py:72, cli.py:176 (and everywhere), adapters/mock.py:8 (only path), ingestion/parser.py
- Spec reference: build-roadmap.md:65 and V1 "deterministic source hierarchy parsing as the V1 ingestion baseline"; consolidated-requirements-and-design.md:384 ("Current ingestion is not yet the target LLM-assisted semantic ingestion pipeline."); improvements-and-enhancements.md:1.1 ("Current ingestion is strictly deterministic... roadmap... explicitly call for LLM-assisted ingestion as a post-V1 step").
- Description: Every code path (CLI ingest-*, worker `process_next`, web upload/process-inbox) hardcodes `MockIngestionAdapter().process(...)`. No pluggable real ingestion adapter is exercised for V1. Deterministic heading/paragraph chunking is the only behavior.
- Evidence: All calls are to `MockIngestionAdapter`; no `IngestionAdapter` base or runtime selection for ingestion (contrast with answer/embedding adapters); parser + mock deliver the hierarchy required by V1, but the "scaffold" nature is repeatedly called out in specs as non-target.
- Suggestion: Keep mock as default for reproducibility (as proposed in improvements 1.1); add the review-gated LLM path without changing V1 baseline.
- Status: open

### Issue 6 -- Severity: nit | gap (edge case)
- File: src/tirzah/ingestion/dates.py:124 (`filesystem_date_candidates` uses st_ctime/st_mtime), cli.py:112 (chronological plan)
- Spec reference: build-roadmap and consolidated-requirements-and-design.md:453-458 (earliest credible date priority explicitly lists "original file creation date... modification date, when preserved by the source acquisition path" as weak fallbacks; "Filesystem timestamps are weak... especially for web-staged uploads").
- Description: Date analysis correctly prioritizes explicit content > filename > fs dates and annotates candidates + selected origin on the document. However, the code unconditionally includes ctime/mtime even for staged uploads (where they reflect import time).
- Evidence: `analyze_source_dates` always appends both fs candidates; `selected_origin_date` prefers earlier sources; activity logs report "candidate_count"; README and specs document the weakness.
- Suggestion: Add a config or per-run flag to deprecate fs dates for uploaded/staged sources; surface "weak evidence" more prominently in UI/CLI logs.
- Status: open

### Issue 7 -- Severity: bug (partial recovery)
- File: src/tirzah/db/repositories.py:61-66 (commit_ingestion try/except cleanup), 111-118 (rebuild_document)
- Spec reference: build-roadmap Stage 1 "transaction-like commit boundary"; consolidated "Repository should be reproducible from source documents".
- Description: Commit does a best-effort rollback (delete nodes/trees/document + graph edges on exception after partial inserts). Rebuild does replace + restore on failure. Not atomic (no Mongo transaction or two-phase); partial state is possible under concurrent load or certain failures. `insert_tree_nodes` embeds during the loop (line 190).
- Evidence: Rollback deletes by document_id after the nodes loop fails; tests/test_repositories.py:59 explicitly tests the rollback path; no `with db.client.start_session()` or multi-doc txn visible; embedding happens inside the insert loop (not after commit).
- Suggestion: Document the non-transactional nature (already partially in build-roadmap post-V1 list); consider batching embeds separately or using epoch + supersede for safety.
- Status: open

### Issue 8 -- Severity: suggestion
- File: src/tirzah/ingestion/worker.py:208-210 (attach then mutate completed), cli.py:165 (similar pattern)
- Spec reference: Transparency + "every ingestion job should produce an understandable activity log".
- Description: After `attach_ingestion_activity(completed, report)`, the caller mutates `inserted["activity_report"] = ...`; similar patterns elsewhere. Fragile if the dict shape changes.
- Evidence: worker.py:209 `inserted["activity_report"] = completed["activity_report"]`; `complete_job` then stores the mutated result.
- Suggestion: Return a single enriched structure from `process_next` / `ingest_source_path` and avoid post-attach mutation.
- Status: open

---

## Retrieval & Context

### Issue 9 -- Severity: gap (matches known open item)
- File: src/tirzah/sessions/interaction.py:587 (`direct_retrieval_decision`), 598 (`select_focus_node`), retrieval/queries.py:61 (search_nodes lexical + near-match + usage bonus)
- Spec reference: consolidated-requirements-and-design.md:307-311 ("Required next improvements: better explanation when retrieval is skipped; less dependence on lexical regex candidate collection."); build-roadmap:618 ("Direct retrieval still needs explicit intent classification and relevance thresholds.").
- Description: Intent classification exists (low_intent guard, active-doc reference, "should_search_corpus") and a `DIRECT_CONTEXT_MIN_SCORE` constant (72), but broad corpus search for generic substantive prompts still relies on lexical scoring + usage bonus + near-match fallback with no hard relevance gate before including nodes. Controller decision traces help, but "over-match" risk remains.
- Evidence: `node_search_score` (queries.py:166) uses hard-coded bonuses/penalties; `prepare_direct_answer_prompt` falls back to source excerpts or no-context; improvements 2.3 calls out "less dependence on lexical regex".
- Suggestion: Promote the existing min-score / decision logic into a stricter, configurable gate with explicit "retrieval_skipped_reason" surfaced in every activity log.
- Status: open

### Issue 10 -- Severity: strength | gap (explanatory only)
- File: src/tirzah/retrieval/trust.py (full), queries.py:195 (usage_score_bonus), interaction.py:664 (compact_trust_diagnostic_for_node attached to retrieval_output)
- Spec reference: build-roadmap V1 must-include "trust/temporal diagnostics as explanatory signals, not ranking authority"; consolidated:560 ("Current trust/temporal diagnostics are explanatory and visible in retrieval traces. They do not yet affect retrieval ranking."); improvements 3.1.
- Description: Full `trust_temporal_diagnostic` (endorsement, recency decay via profile, frequency/usage, verification) is computed, attached to traces/activity, and exposed via CLI `trust-diagnostic` + web. It is **never** used in `node_search_score`, `embedding_candidate_sort`, or context ordering.
- Evidence: `node_search_sort_key` only uses search_score + last_used + text_len; trust components appear only in `retrieval_output["trust_diagnostic"]` and controller sections.
- Suggestion: Per improvements-and-enhancements.md:3.1, add opt-in bounded boost after provenance/usage ordering; keep conservative default.
- Status: open

### Issue 11 -- Severity: nit (error handling)
- File: src/tirzah/retrieval/queries.py:400 (scan loop), 313 (embedding_candidate_report), interaction.py many `except Exception`
- Spec reference: Quality + transparency; agentic repair guidance exists.
- Description: Broad `except Exception` in many retrieval paths (prepare, agentic loop, adapter calls) log to process_trace but sometimes produce terse "retrieval_failed" without full provenance of which stage or which node caused it. Embedding candidate scan has good per-exclusion counters but the top-level error path is coarse.
- Evidence: interaction.py:192 `except Exception as error: ... "retrieval_failed"`; similar in agentic:397 and answer_adapter:259; queries has fine-grained diagnostics inside functions but outer callers collapse.
- Suggestion: Enrich the process_trace step with stage + partial state (already partially done for controller/repairs); surface stage in the plain activity log.
- Status: open

---

## Sessions / Continuity / Endorsement / Output

### Issue 12 -- Severity: strength
- File: src/tirzah/sessions/output_ingestion.py:195 (`commit_output_job` -> `commit_ingestion`), endorsements.py:41 (explicit `update_node_endorsement` with review_history), exchanges.py:86 (queue + link)
- Spec reference: build-roadmap V1 must-include "generated-output ingestion as unreviewed memory, with explicit review controls"; consolidated:491-502 ("Generated output must not become trusted source memory automatically. Review states include unreviewed / implicit / explicit / rejected.").
- Description: Answers are always queued via `queue_exchange_output` (content-hash dedup), processed into `generated_output` + `llm_answer` labeled nodes (unreviewed), and can only become endorsed via explicit `endorse-node` / review API that writes `provenance` + `metadata.review_history`. No auto-endorsement.
- Evidence: `output_job_to_ingestion_result` builds two nodes with `DEFAULT_ENDORSEMENT_LABEL`; `process_next_output_ingestion` calls the guarded `commit_ingestion`; endorsement path rejects non-`generated_output` nodes and requires valid label.
- Suggestion: (Strength) Add the natural-language hint proposal from improvements 6.1 as a read-only suggestion surface.
- Status: open (strength)

### Issue 13 -- Severity: gap (matches spec open item)
- File: src/tirzah/sessions/exchanges.py:62 (record_active_documents), interaction.py:588 (active document scoping), active_documents.py:48 (upsert)
- Spec reference: consolidated-requirements-and-design.md:274 ("Implementation status: open. Exchange records now carry domain IDs, but last prompt iteration records and the continuity panel still need to be built."); build-roadmap V1 "active document references can support follow-up prompts such as 'this document'".
- Description: Active documents + "this document" / source-fallback work for the smoke test and basic continuity. However the richer "last prompt iteration record" (prompt, intent, retrieved + rejected, full context package, unresolved items) is not a first-class persisted artifact separate from the exchange + context_metadata.
- Evidence: `save_exchange` writes `context_metadata` + `used_node_ids`; no dedicated continuity collection or `session.last_prompt_iteration`; improvements 5.1 calls it high priority.
- Suggestion: Implement per spec before declaring continuity complete.
- Status: open

### Issue 14 -- Severity: nit (resilience)
- File: src/tirzah/sessions/exchanges.py:81 (record_node_usage after insert), output_ingestion.py:171 (update after commit)
- Spec reference: build-roadmap notes "saved exchange usage-summary updates are written before output-ingestion queue linking, keeping scored_node_count aligned even if output queueing fails".
- Description: Usage scoring happens before output queue; on output failure the exchange is still marked with scored count. Good. However, several post-save updates (`scored_node_count`, `output_ingestion_job_id`, active docs) are separate writes without the original insert being in a txn.
- Evidence: Three distinct `update_one` after the `insert_one` for the exchange.
- Suggestion: Accept as current (observational) model; add comments referencing the non-tx nature documented in roadmap.
- Status: open

---

## Graph / Semantic / Governance

### Issue 15 -- Severity: strength
- File: src/tirzah/db/repositories.py:473 (create_reviewed_semantic_edge with duplicate check + provenance), 1001 (review_semantic_edge_candidate), 745 (batch enqueue with dry-run), retrieval/queries.py:247 (semantic_candidate_nodes + embedding)
- Spec reference: build-roadmap V1 must-include "reviewed semantic-edge candidate queue, accept/reject workflow, and reviewed graph-edge promotion"; "one-hop proximity and bounded graph-path inspection".
- Description: Label-overlap + embedding-similarity candidates can be enqueued (CLI + web + batch), listed, accepted (promotes to `graph_edges` with reviewer/note/embedding evidence) or rejected. Structural `contains` backfill exists. Proximity/paths/edges exposed in CLI and web. Full provenance on reviewed edges.
- Evidence: `review_semantic_edge_candidate` calls `create_reviewed_semantic_edge` only on pending + records review; duplicate edge and candidate guards; `expand_proximity` / `expand_graph_paths` / `graph_edges_for_node` all implemented and used by memory-agent tools.
- Suggestion: (Strength) Good foundation; next per improvements 3.2 add `contradicts` candidate source.
- Status: open (strength)

### Issue 16 -- Severity: gap (per roadmap)
- File: src/tirzah/db/governance.py (full — read-only list/get + seed + process run create/update), sessions/interaction.py + worker (create "answer_query" / "ingest_source" runs), no enforcement logic
- Spec reference: consolidated-requirements-and-design.md:544 ("Current process-run persistence is observational. Answer and ingestion flows create/update process runs where possible, but process rules are not yet enforced."); build-roadmap known gaps; governance-schema-plan referenced but not in V1 must-include.
- Description: Process runs, agent identities, trust profiles, policies, and process objects are seeded, listable, updatable, and attached to answer/ingest flows. No code path enforces "mandatory steps", "approval points", or "behavioral expectations" — all are passive observation + manual CLI/web creation.
- Evidence: `start_answer_process_run` / `finish...` wrap the flow but never consult `get_process_object` for rules; `update_process_run` just appends; improvements 6.2 proposes "begin enforcing simple process objects" as low-to-medium.
- Suggestion: Keep observational for V1; surface in activity logs when a process object exists for the current flow.
- Status: open

---

## Adapters / Config / Hoglah

### Issue 17 -- Severity: nit (complexity)
- File: src/tirzah/adapters/hoglah_runtime.py (full — _CallbackReceiver with threading.Lock + events, HoglahJobRunner poll + callback), answer.py:135, embedding.py:321
- Spec reference: ADR-019 (detailed operator-sanctioned topology); consolidated Local Boundary.
- Description: Hoglah integration is recent and correctly implemented as pure-submitter + separate daemon (local SQLite + poll/callback). However it introduces threading HTTP server, locks, close semantics, and two delivery modes inside the memory-critical path.
- Evidence: `_CallbackReceiver` uses `ThreadingHTTPServer` + per-job Events; `close()` + `__del__`; multiple places that must remember to close runners.
- Suggestion: Keep well tested; consider a higher-level "delivery strategy" abstraction if more external queues appear.
- Status: open

### Issue 18 -- Severity: claim-mismatch (naming)
- File: src/tirzah/config.py:12 (MongoConfig database default "mnemosyne_dev"), cli.py:1088, many internal strings
- Spec reference: consolidated-requirements-and-design.md:181-189 (Product Naming: "target product name is Tirzah"; "Python package, CLI command, UI labels, config names... should move"; "old mnemosyne may remain as temporary compatibility"); README notes preferred `tirzah`.
- Description: Package is `tirzah`, CLI entry `tirzah`, but many defaults, DB name, some comments, and egg-info paths still reference the old `mnemosyne` name. `mnemosyne` compatibility path exists.
- Evidence: `database: "mnemosyne_dev"` in model; import paths under tirzah now; README explicitly says "The preferred CLI is now `tirzah`".
- Suggestion: Finish the rename slice (config defaults, docs, internal strings) as a dedicated non-feature change.
- Status: open

---

## Web UI / CLI Surface / DB Layer

### Issue 19 -- Severity: strength
- File: src/tirzah/web/app.py:184 (work vs ?developer=1), 862 (/api/ask), static/ (tabs), cli.py (full command surface from db-ping through endorse-node, graph, semantic review, health, etc.)
- Spec reference: build-roadmap V1 "FastAPI web UI with normal work mode and developer mode; Ask, Browse, and Ingestion workspaces"; "readable activity logs... raw JSON kept as developer detail"; "CLI and web UI expose enough inspection...".
- Description: Default root is clean Ask (prompt/response/log). Developer reveals Browse/Ingestion + raw controls. All V1 inspection surfaces (documents, nodes, sessions, exchanges, active docs, semantic candidates, graph edges/paths/proximity, process runs, profile jobs, output ingestion, governance) are present in both CLI and web.
- Evidence: Smoke.md + v1-readiness checklist exercised both; work mode hides JSON; many `@app.get/post` + CLI subparsers cover the exact list in build-roadmap:55.
- Suggestion: (Strength) Excellent surface completeness.
- Status: open (strength)

### Issue 20 -- Severity: gap (post-V1 items in V1 surface)
- File: src/tirzah/cli.py:759 (`memory-health`), web/app.py:192, db/health.py (full)
- Spec reference: improvements-and-enhancements.md:12.1 (living "Memory Health" report) and build-roadmap post-V1 list; v1-readiness claims inspection completeness.
- Description: `memory-health` / `/api/memory-health` is implemented and useful (totals, profile %, queues, attention list). It was listed as post-V1 proposal but shipped in the V1 surface.
- Evidence: Present in README command list, CLI parser, web, health.py; v1 checklist does not explicitly require it but claims "CLI and web UI expose enough inspection".
- Suggestion: Document as V1+ bonus or move the proposal note.
- Status: open (minor claim-mismatch)

### Issue 21 -- Severity: bug (edge / error)
- File: src/tirzah/db/repositories.py:149 (mark_document_tree_nodes_status iterates and updates one-by-one), 403 (backfill_structural...), many `if not hasattr(db, "graph_edges")`
- Spec reference: build-roadmap "real MongoDB persistence behind a documented memory-store boundary"; robustness for V1.
- Description: Several maintenance paths (supersede marking, structural backfill) do N+1 style finds + updates instead of bulk. Graceful `hasattr` checks for optional collections (graph_edges, semantic_*, etc.) allow running without them but scatter defensive code. No central "required collections" list.
- Evidence: `mark...` does per-row `update_one`; `backfill_structural...` loops with per-child parent lookup; repositories and health are full of `if not hasattr(db, "...")`.
- Suggestion: Centralize collection requirements or use a schema bootstrap; batch the supersede updates.
- Status: open

---

## Tests & Smoke / Overall Correctness

### Issue 22 -- Severity: strength
- File: tests/ (test_interaction.py, test_repositories.py, test_worker.py, test_web_app.py, test_endorsements.py, v1 smoke fixture + docs/v1-release-candidate-smoke.md), pytest 481 passed per checklist.
- Spec reference: build-roadmap V1 completion gate "full automated tests pass"; smoke procedure.
- Description: Broad unit coverage of happy + error paths (duplicate, rollback, agentic tool loops, endorsement, output ingestion, retrieval queries, worker claim/retry/dead-letter). Smoke fixture + documented CLI+web sequence exercises the end-to-end gates.
- Evidence: Explicit rollback test in test_repositories; interaction fakes cover direct/agentic + low-intent guard; smoke.md matches v1-readiness exactly and was run 2026-06-13.
- Suggestion: (Strength) Add golden-context regression on the smoke corpus for retrieval ordering/budgeting as proposed in improvements 11.2.
- Status: open (strength)

### Issue 23 -- Severity: nit (test realism)
- File: tests/test_interaction.py (heavy Fake* collections), many other tests
- Spec reference: build-roadmap "local test suite passing from a clean checkout with documented setup"; ADR-011 "Use real MongoDB in Stage 1".
- Description: Most behavioral tests use in-memory fakes; integration with real Mongo is present in smoke/CLI paths and some worker/repository tests, but the bulk of the suite does not exercise the actual persistence shape or indexes.
- Evidence: `FakeDb`, `FakeCollection`, `FakeNodeCollection` etc. in test_interaction and test_repositories; `get_database` + real client only in smoke and a minority of tests.
- Suggestion: Keep fakes for unit speed; ensure a "real-mongo" marker or docker-compose test profile exists so the smoke-level paths are exercised in CI-like runs.
- Status: open

---

## Executive Summary

The Tirzah V1 "Local Memory Workbench" implementation is remarkably faithful to the documented scope for a scaffolded system. Core principles of **Source Authority** (verbatim text + chunk-level provenance + checksum/archive), **Transparency First** (excellent human-readable activity logs as the default), and **Local Memory Interface Boundary** (strict guards + Hoglah IPC model) are upheld in the primary paths. V1 must-include features (real Mongo, CLI+web ingestion with dedup/dead-letter, full persistence of the required collections, deterministic hierarchy, profile jobs + semantic review, direct+agentic retrieval, generated-output review, active documents, graph inspection, work/developer UI modes, readable logs, tests + smoke) are all present and exercisable.

The primary mismatches are:
- Several items listed as "open" or "post-V1" in the canonical consolidated/build-roadmap documents (rich continuity records/panel, trust effects in ranking, process enforcement, LLM-assisted ingestion, full rebuild comparison/GC, stricter retrieval thresholds) are treated as complete in `v1-readiness-checklist.md` because the CLI/web inspection surfaces and basic flows exist.
- Ingestion remains 100% deterministic mock (correct per V1 baseline, but the checklist and claims sometimes read as if the target pipeline is closer).
- Non-atomic writes and broad exception handling are the norm (acceptable for local V1 but noted in the roadmap gaps).

No silent source rewriting, no HTTP in memory paths for normal operation, and provenance is carried at the right granularity. The system is already a solid local memory backend.

## V1 Gates Assessment (matching v1-readiness-checklist.md exactly)

| Gate | Assessment | Evidence / Gap |
|------|------------|----------------|
| `tirzah db-ping` verifies local MongoDB | **Implemented & correct** | cli.py:1090 + client.py + ensure_indexes; smoke passes. |
| Fresh text/MD source staged/processed/archived/inspected (CLI+web) | **Implemented** | Full paths in cli ingest-*, worker, web upload/process-inbox, list/show-tree/search/node-context etc. Smoke uses fixture. |
| Duplicate/failed paths to expected dead-letter | **Implemented** | files.py move, worker.py:79/154 (duplicate/failed), queue reject/fail, tests cover. |
| Profile-backfill status (absent/partial/blocked/ready) | **Implemented** | embedding_backfill jobs + status in ingestion_status + CLI `profile-backfill-jobs` + web. |
| Profiled source → semantic candidates + accept/reject (CLI+web) | **Implemented** | enqueue-*/semantic-edge-candidates, review-semantic-edge-candidate, vector/profile variants, promote to graph_edges. Smoke exercises. |
| Direct ask → saved answer + readable activity log + diagnostics | **Implemented** | answer_query direct path, save_exchange, attach_answer_activity, activity_report/log, controller decision. |
| Agentic ask exposes planner/tool trace | **Implemented** | run_memory_agent_loop + traces in process_trace; smoke JSON check. |
| Generated output → unreviewed memory + explicit endorse/reject | **Implemented** | output_ingestion queue + commit + endorsements.update_node_endorsement with history. |
| Active doc refs support "this document" follow-ups | **Implemented** | active_documents.py + select_active_document_focus_node + source fallback in interaction. Smoke passes. |
| CLI+web inspection for docs/nodes/sessions/exchanges/active-docs/semantic-cands/graph-edges/profile-jobs/process-runs | **Implemented** (breadth complete) | Full CLI surface + web Browse/Ingestion tabs + governance + health. Developer mode. |
| Default web UI does not require raw JSON | **Implemented** | Work mode + plain activity_log default; developer toggle for raw. |
| Full automated tests pass | **Implemented** (481 per checklist) | Suite + smoke procedure documented and executed 2026-06-13. |

**Verdict on gates:** All gates have working code paths and were smoke-verified. Several are "implemented at V1 scaffold depth" rather than the richer target described in the same documents (see gaps above). The checklist's "Done / None" column over-states completeness relative to the "Known gaps after reconciliation" and "Open implementation items" sections in build-roadmap + consolidated.

## Strengths
- Verbatim source preservation + chunk provenance + archive + dedup is exemplary and directly satisfies "Source Authority".
- Readable activity logs (ingestion + answer) are first-class and the default UI surface — a beautiful match to "Transparency First".
- Local boundary is actively enforced in code + config + ADR, not just documented.
- Non-destructive epoch + superseded rebuilds + semantic review workflow + active documents + output review are all present and review-gated.
- CLI is extremely broad and useful for operators; web cleanly separates work vs developer.
- Smoke fixture + procedure + test coverage of error paths (rollback, duplicate, tool repair, low-intent guard) is strong for the phase.

## Critical Gaps / Risks
- **Continuity & Last Prompt Iteration** (consolidated:274): Active documents are a useful skeleton but the spec's richer per-cycle record + UI panel is missing. Follow-ups beyond "this document" will feel thin.
- **Retrieval quality** (consolidated:307, improvements 2.x): Still heavily lexical/regex + usage bonus with explanatory-only trust. Generic prompts can over-retrieve; no hard relevance gate. This is the highest user-visible quality risk inside V1 scope.
- **Observational governance/process** + no ranking effect from trust: Correct per "V1 narrow" but creates a gap between the "Memory as Cognitive Infrastructure" principle and current behavior.
- **Rebuild / repository refresh** is versioned but lacks the comparison, audit, and GC tooling called for in the refresh model.
- **LLM-assisted ingestion** is explicitly post-V1 yet the deterministic mock is the only path; future adapters must preserve the exact-text invariant.
- Scattered `hasattr` collection checks and N+1 maintenance loops indicate the "documented memory-store boundary" is more aspirational than a single clean facade (MemoryStore is read-only helper; most writes go direct).
- Hoglah adds valuable durability but increases the surface that must stay "local IPC" only.

## Recommendations (including items now high-priority from improvements-and-enhancements.md)
1. **Immediate post-V1 (high leverage per the living doc):** 2.1 Hybrid lexical+vector search (embeddings already backfilled), 3.1 Actionable trust/temporal ranking (opt-in), 5.1 Last prompt iteration / continuity panel, 4.1 More robust memory-agent planner + stricter validation, 7.2 Per-node "why included" inspector.
2. Treat the v1-readiness checklist as "surface complete" and publish a companion "V1 known limitations" note that quotes the open items from consolidated + build-roadmap.
3. Add a real (but review-gated) ingestion adapter path while keeping Mock as the reproducible default.
4. Strengthen rebuild tooling (diff, compare, GC) and document the non-transactional commit model.
5. Finish product rename hygiene (config defaults, internal strings).
6. Add a "real Mongo + profile + local model" integration test profile so the smoke-level paths are not only exercised by humans.
7. Prioritize retrieval guardrails and explanation quality over new surfaces; this directly serves "Quality Before Speed" and user trust.

The implementation is a solid, transparent local memory workbench that already exceeds many "V1" expectations in provenance and inspectability. The remaining work is largely the "strengthening" items already enumerated in improvements-and-enhancements.md rather than missing core V1 features.

---

**Review file written to:** `/tmp/tirzah-requirements-to-implementation-review.md`
