# Requirements And Design Addendum

Date: 2026-05-21

Status: implementation addendum. This document records changes, additions, and inferred design constraints that have emerged during prototype work. It does not replace the original requirements or technical design; where there is conflict, the original requirements remain authoritative unless a later explicit decision updates them.

## Purpose

The original requirements describe the target Mnemosyne architecture: local-first memory, Gemma-style memory-agent retrieval, MongoDB graph storage, compiled context, final reasoning model separation, session continuity, endorsement, and consolidation.

The current implementation has added several scaffold features and operational decisions that were necessary to make the system usable before the full architecture exists. These are listed here so future work can distinguish:

- original requirements;
- accepted implementation decisions;
- temporary prototype scaffolds;
- inferred requirements that should be confirmed or promoted later.

## Implemented Additions

### Local Runtime And Interface

- Python 3.12-compatible project scaffold with editable virtual environment.
- Real local MongoDB-backed persistence from the beginning of Stage 1.
- FastAPI plus vanilla static web UI.
- Local web URL: `http://127.0.0.1:8765/`.
- Runtime API exposing adapter/model defaults and direct/agentic retrieval choices.
- UI controls for search, ask, session selection/creation, queue status, recent jobs, inbox processing, adapter selection, model selection, and retrieval mode selection.

### Ingestion And Source Preservation

- Markdown/plaintext ingestion exists as deterministic scaffold ingestion, not final Gemma-driven ingestion.
- Accepted sources are copied into `data/archive/` by SHA-256 checksum.
- Duplicate files are rejected by checksum.
- Inbox processing moves accepted files to `data/staging/processed/`.
- Duplicate and failed jobs are moved to `data/dead_letter/duplicate/` and `data/dead_letter/failed/`.
- Source-derived headings and paragraph text are preserved. Source cleanup, deduplication, compression, or summarisation is not an accepted ingestion strategy.
- Nodes, trees, and documents use `schema_version: 1`.
- Node records include scaffold fields for summary, relations, proximity, usage score, and continuity-critical state.
- Ingestion relation hints that reference known node keys are persisted as first-class `graph_edges` records with source/target node IDs, relation type, weight, confidence, direction, provenance, and document/tree scope.
- Existing parent/child node links can be backfilled as structural `contains` graph edges. These are provenance-marked as derived from stored hierarchy, not semantic inference.
- Graph status diagnostics report total edge count plus relation-type and provenance-source breakdowns.
- Semantic-candidate diagnostics are read-only: they rank nodes sharing meaningful non-structural labels, excluding source-root containers, but do not write inferred graph edges.
- Semantic-edge candidate queue commands can enqueue pending review rows from semantic-candidate diagnostics and list them for batched operator review.
- Semantic-edge candidate review can accept a queued candidate into a reviewed graph edge or reject it with reviewer/note metadata.
- FastAPI and the web operator panel expose semantic-edge candidate listing and accept/reject review actions.
- Reviewed semantic-edge promotion is operator controlled: a CLI command can create a typed directed edge between two known nodes with reviewer/note provenance, shared-label evidence, duplicate protection, and bounded weight/confidence. Memory-agent tool calls remain read-only.
- Proximity and graph-path summaries expose reviewed-edge provenance diagnostics so traces can show when semantic review, rather than document structure alone, influenced traversal.
- Label definitions are stored in MongoDB.

### Corpus Imports

- Original Mnemosyne requirements and technical design documents are imported.
- Project Gutenberg `Memory: How to Develop, Train, and Use It` is imported as `memory_reference` public-domain test/reference corpus.
- AMS corpus is imported as a large `ams_domain` corpus.
- A Wikipedia Taj Mahal extract is imported as ignored local online test content with labels `online_test`, `taj_mahal`, and `wikipedia`.
- Imported online source files under `data/online_sources/` are local runtime data and are ignored by git.

### Retrieval And Context Assembly

- Direct retrieval supports text, label, endorsement label, document ID, and created-at bounds.
- Direct text search now expands natural-language queries into exact phrase plus content-term filters.
- Direct text search uses whole-term matching for extracted terms and a compact-node tie-breaker for equal scores.
- Direct ranking demotes empty/title-only chunks, source roots, metadata headers, separator-only matches, and oversized source-section containers.
- Direct ranking boosts human endorsement labels as an interim ordering hint, penalizes rejected nodes, and slightly demotes unreviewed generated output.
- Direct ranking applies a capped `usage_score` boost and `last_used_at` tie-breaker so successful prior context can surface without overriding rejection/provenance controls.
- Context compilation can gather focus, ancestors, siblings, and descendants.
- Rendered context uses full node text subject to a character budget.
- Prompt envelopes include context text, system instruction, budget metadata, and included/skipped context metadata.

### Agentic Retrieval Scaffold

- Agentic mode separates the memory-agent role from the final answer role at the configuration/orchestration layer.
- Memory-agent calls can use a separate adapter/model from the final answer adapter/model.
- Memory-agent prompts include the current session ID and compact active document summaries.
- Memory-agent query guidance uses active document titles, source paths, and labels as bounded near-match vocabulary for the current session.
- The memory-agent receives an injected tool interface for:
  - `search_nodes`;
  - `compile_context`;
  - `get_node_context`;
  - `get_document`;
  - `get_document_tree`;
  - `get_graph_edges`;
  - `expand_proximity`;
  - `expand_graph_paths`;
  - `semantic_candidates`;
  - `list_active_documents`;
  - `list_documents`.
- Exact lookup tools let the planner inspect known document IDs, document tree structure, node parent/child context, typed adjacent graph edges, one-hop proximity-ranked graph neighbors, bounded multi-hop graph paths, and read-only semantic candidates without re-querying by text. Proximity, graph-path, and semantic-candidate expansion compile context for the top target nodes before final answer assembly. Document-tree lookup is treated as navigation metadata and does not by itself mark every tree node as used evidence for scoring.
- CLI graph inspection commands expose the same single-hop edge, one-hop proximity, bounded multi-hop path expansion, semantic-candidate diagnostics, candidate queue, candidate accept/reject, and reviewed semantic-edge promotion helpers for operator diagnostics.
- The memory-agent is instructed to return strict JSON decisions with either `continue` plus tool calls or `done`.
- Python executes tool calls, records structured tool results, and feeds compact summaries back into later memory-agent iterations.
- Proximity and graph-path summaries expose node previews, scores, and compact edge metadata so later memory-agent iterations can choose follow-up exact lookup or context compilation calls.
- Planner failure or no initial tool calls triggers a conservative fallback search.
- Agentic final answer assembly can include up to two compiled search, proximity, or graph-path contexts, deduplicate repeated node records, and enforce a shared context budget.
- Agentic final answer assembly now stores a structured `context_document` in prompt context metadata, including query, tool results, search diagnostics, assembled records, and capped document-tree navigation output. The final answer prompt remains Markdown-rendered for current models.
- Search diagnostics are included after compiled source context so source evidence remains primary.

### Query Assembly

- Python builds deterministic query assembly before memory-agent prompting and tool execution.
- Query assembly currently includes:
  - lexical content terms;
  - adjacent exact phrases;
  - named anchors;
  - bounded near-match terms when a comparison vocabulary is available;
  - suggested fallback searches.
- Query assembly is injected into memory-agent prompts.
- Query assembly and fallback probe counts are surfaced in process traces and final answer diagnostics.
- This is a pragmatic bridge toward semantic retrieval, not a replacement for graph traversal, embeddings, or the semantic map.

### Observability And Persistence

- Ask/chat/history flows persist exchanges.
- Exchange usage-summary updates are persisted before output-ingestion queue linking, reducing score/summary drift if output queueing fails.
- Saved exchanges update a session-scoped active document registry from answer `used_node_ids`.
- Saved exchanges increment `usage_score` and update `last_used_at` on used non-rejected nodes after the exchange row exists; exchange history records the number of nodes updated as `scored_node_count`.
- Serialized node search results expose raw `usage_score`, capped `usage_score_bonus`, and `last_used_at` for retrieval diagnostics.
- Active document records preserve document ID, title, source metadata, labels, referenced node IDs, and reference counts.
- Active document label metadata accumulates across repeated references instead of being overwritten by the latest used-node batch.
- Active document API/CLI serialization returns stable sorted labels and node IDs.
- Agentic retrieval exposes active documents to the memory-agent through prompt context and a read-only `list_active_documents` tool.
- Direct retrieval scopes reference-shaped session prompts such as "this document" or "previous source" to the session's active documents before falling back to broad corpus retrieval, checks all active documents for topical matches, and can use the first active document root/default node when no active document has a topical match.
- Direct retrieval can use a bounded local source-document fallback for reference-shaped active-document prompts when no Mongo focus node is available. The fallback reads the active document's archived/source file path, injects a capped excerpt into the final-answer prompt, records `source_fallback` metadata, and keeps `included`/`used_node_ids` empty so node usage scoring is not distorted.
- Saved answer text is captured in `output_ingestion_queue` as pending `llm_answer` work with exchange/session provenance, used node IDs, active document IDs, adapter/model metadata, and a content hash.
- Pending output-ingestion jobs can be processed into unreviewed graph documents, trees, and nodes labelled `generated_output` and `llm_answer`.
- Generated-output nodes can be explicitly reviewed through CLI/API, updating `endorsement_label`, `provenance.endorsement_label`, and node review metadata/history.
- Output ingestion does not yet infer endorsement, semantic relations, proximity, or REM consolidation from generated text.
- Web and CLI answer calls expose structured process traces.
- Process traces include prompt intake, memory-agent iterations, tool calls, retrieval/context assembly, and answer adapter execution.
- `.restart.md` is maintained manually as a working restart note; it is still not the required graph-backed restart state source of truth.

## Inferred Requirements

These are not yet fully formalised in the original documents, but the implementation has shown they are needed.

### Memory Engine As Backend

Mnemosyne should remain a durable memory backend that can be used by several interaction surfaces rather than becoming one closed assistant shell.

Reason:

- Web import, coding support, CLI agency, and voice prompting have different user interfaces but need the same source preservation, graph retrieval, context assembly, review, endorsement, and restart-state mechanisms.
- Existing FOSS tools may already cover parts of capture, UI, speech recognition, or agent orchestration.
- Keeping stable local APIs makes it possible to plug Mnemosyne into those tools without giving up memory authority.

Constraint:

- External tools may fetch content, transcribe audio, or execute commands, but Mnemosyne should own durable memory writes, provenance, retrieval scoring, context envelopes, and endorsement state.
- See `docs/practical-applications.md` for the current application lanes and FOSS integration criteria.

### Deterministic Retrieval Sidecar

The system needs a deterministic lexical/fuzzy retrieval sidecar even after semantic retrieval exists.

Reason:

- It provides predictable cold-start behaviour.
- It makes tests stable.
- It gives the memory-agent visible fallback probes.
- It catches exact names, source titles, document labels, and short factual questions that embeddings may blur.

Constraint:

- This sidecar should remain an evidence-gathering aid, not the final authority on memory relevance.
- Final memory selection should move toward memory-agent/tool orchestration plus graph/semantic traversal.

### Query Assembly As Shared Interface Contract

The memory-agent and deterministic fallback should share the same query interpretation artifact.

Reason:

- The planner needs to see the same lexical terms, phrases, anchors, and fallback probes that Python will use.
- Tool traces need a compact explanation of why a search ran and how it was ranked.
- Later semantic expansion can attach to the same contract instead of adding another opaque layer.

Current and potential future fields:

- near-match token candidates;
- fuzzy token variants;
- synonym/sense candidates;
- embedding candidate IDs;
- semantic-map sense IDs;
- active-document hints;
- provenance/label constraints;
- negative intent terms;
- confidence and ambiguity notes.

### Natural-Language Search Must Not Require Exact Prompt Text

Direct search and memory-agent search tools must handle question-shaped input without requiring exact stored wording.

Reason:

- Users naturally ask questions, not keyword probes.
- The memory-agent may preserve full user intent in search calls.
- UI search is also used as a diagnostic surface for retrieval quality.

Current scaffold:

- exact full-query regex remains available;
- content terms are extracted after stopword filtering;
- term filters use whole-term regex;
- local ranking reorders Mongo candidates.

Known limitation:

- Mongo candidate collection is still lexical regex based and limited before Python reranking. Broader or older relevant matches can still be missed until embeddings, graph traversal, or better indexed search are added.

### Source-Preservation Rule

Prototype convenience must not silently delete, rewrite, compress, or summarise source documents during ingestion.

Reason:

- Endorsement and provenance are chunk-level.
- Later graph repair requires source-faithful reconstruction.
- The requirements expect mapping, labelling, scoring, and versioning rather than destructive cleanup.

### Online Test Content Handling

Online imports used for tests should be stored as local source files with attribution and labels, then ingested through the normal pipeline.

Reason:

- The system should exercise the same provenance path as local files.
- Fetched content may be licensed or mutable.
- Runtime source files should not be committed unless explicitly intended as fixture data.

Current example:

- Taj Mahal Wikipedia extract, retrieved 2026-05-21, stored under `data/online_sources/`, ingested with attribution and labels.

## Design Notes From jscompare Review

Repository reviewed:

- `https://github.com/CelloSounds/jscompare`
- Commit inspected: `a4097d0`
- License: MIT
- Main file: `jscompare.js`

`jscompare` is a lightweight JavaScript natural-language comparison helper. It splits two strings into words, compares every primary word with every secondary word, uses `diff_match_patch` Levenshtein distance, rejects pairs above a threshold, and accumulates weighted scores for near matches.

It is not a semantic model:

- no embeddings;
- no synonym model;
- no ontology or graph;
- no phrase/sense disambiguation;
- no context-window understanding.

However, it is relevant as a design pattern for semantic elasticity:

- add typo-tolerant and morphology-tolerant lexical matching before semantic retrieval is available;
- expose near-match scores as diagnostics rather than hiding them inside model prompting;
- keep fuzzy matching deterministic and bounded;
- treat fuzzy lexical results as candidate expansion, not final truth.

Implemented first adaptation:

- query assembly now has `near_match_terms`;
- near matches are generated only against a bounded vocabulary, currently label definitions, existing node labels, document titles, and source paths;
- near-match candidates are used only after an empty initial search in direct focus selection and agentic/tool search;
- session active documents are included in memory-agent near-match guidance and agentic/tool fallback vocabulary;
- fallback probes try exact phrases first, then near-match candidates, then original single terms;
- process traces and prompts expose near matches as diagnostics.

Recommended next adaptation:

1. Do not port `jscompare` directly into production retrieval.
2. Keep the Python helper behind the query assembly layer.
3. Use standard-library or maintained Python options before adding a new dependency.
4. Extend near-match vocabulary gradually to other bounded title/source candidates after active documents.
5. Use near-match expansion only for candidate generation and fallback probes.
6. Cap comparisons aggressively to avoid O(n*m) scans across the full corpus.
7. Keep exact source/provenance ranking above near-token similarity.

Possible first implementation:

- normalize query terms;
- compare query terms only against a bounded vocabulary derived from titles, labels, source paths, or top lexical candidates;
- use edit distance or token similarity to catch near misses such as spelling variants;
- record `{source_term, candidate_term, score, reason}` in query diagnostics;
- let the memory-agent see fuzzy candidates as suggestions, not as retrieved facts.

## Current Non-Requirements Or Deferred Areas

The following are still deferred and should not be implied as complete:

- Gemma-driven ingestion chunking;
- transactional/versioned ingestion commit boundary;
- typed weighted graph edges;
- proximity scoring;
- semantic map and sense clusters;
- embedding/vector search;
- active document registry beyond current used-node capture, read-only memory-agent visibility, narrow direct reference resolution, and bounded source fallback;
- graph-backed restart state node;
- natural-language endorsement detection/writes beyond explicit review controls;
- full traversal scoring feedback, including path scores and unused-path decay;
- REM consolidation;
- output ingestion beyond conservative unreviewed graph insertion;
- broader source-document fallback after retrieval failure, beyond the current active-document-only local-source excerpt path;
- optional web search queued for ingestion.

## Current Project Status Snapshot

As of 2026-05-22:

- Git branch: `main`.
- Latest synced commit before this exchange-consistency slice: `719197b Expose raw node usage scores`.
- Working tree before this addendum: clean.
- Mongo database: `mnemosyne_dev`.
- Mongo counts at status check:
  - documents: 1879;
  - trees: 1879;
  - nodes: 176432;
  - exchanges: 74;
  - sessions: 18;
  - active document rows: 5;
  - output-ingestion rows: 2;
  - queue records: 8.
- Queue status:
  - completed: 5;
  - failed: 1;
  - rejected: 2;
  - pending: 0.
- Web server health endpoint returned ok.
- Current runtime defaults:
  - answer adapter: `ollama_cli`;
  - answer model: `gemma3:1b`;
  - memory-agent adapter: `ollama_cli`;
  - memory-agent model: `gemma3:1b`;
  - retrieval mode: `direct`.
- Current automated suite at last full run: `283 passed`.
- Forward-looking governed cognitive architecture requirements are captured in `docs/mnemosyne-cognitive-architecture-draft.md`; current docs use `Mnemosyne`, with a future product rename likely because of a GitHub name collision.
- Candidate identity, governance policy, process-object, process-run, and trust/temporal weighting schemas are planned in `docs/governance-schema-plan.md`; first read-only CLI/API listing and exact lookup commands, default seed records, memory-agent identity prompt summaries, agentic search exclusion filtering for labels/documents/trees with sampled exclusion diagnostics, answer-process run persistence with blocked-state cleanup for retrieval/planning/adapter/save failures and soft continuation on process-run persistence failures, and read-only trust/temporal diagnostics in retrieval traces with batched search-result annotation exist, but trust/temporal ranking effects and automatic process enforcement are not implemented yet.

## Recommended Next Work

Near-term consolidation:

1. Update roadmap/status docs to reference this addendum.
2. Decide whether deterministic fuzzy lexical expansion should be added before embeddings.
3. If yes, add fuzzy expansion inside query assembly with diagnostics and tests.
4. Keep retrieval improvements framed as scaffold support until graph traversal and semantic map work exist.

Next implementation candidates:

1. Add a confidence threshold for deciding when weak partial matches should still trigger near-match fallback.
2. Broaden near-match vocabulary carefully, next with active documents.
3. Broaden source-document fallback beyond active-document reference prompts.
4. Add active document registry skeleton.
5. Add embedding interface and a brute-force baseline evaluation harness.
6. Evaluate when trust/temporal diagnostics should influence ranking, with a feature flag and before/after tests.
7. Expand process-run integration beyond answer requests into ingestion and semantic-edge review workflows.
