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
- Direct ranking boosts human endorsement labels as an interim ordering hint.
- Context compilation can gather focus, ancestors, siblings, and descendants.
- Rendered context uses full node text subject to a character budget.
- Prompt envelopes include context text, system instruction, budget metadata, and included/skipped context metadata.

### Agentic Retrieval Scaffold

- Agentic mode separates the memory-agent role from the final answer role at the configuration/orchestration layer.
- Memory-agent calls can use a separate adapter/model from the final answer adapter/model.
- The memory-agent receives an injected tool interface for:
  - `search_nodes`;
  - `compile_context`;
  - `list_documents`.
- The memory-agent is instructed to return strict JSON decisions with either `continue` plus tool calls or `done`.
- Python executes tool calls, records structured tool results, and feeds compact summaries back into later memory-agent iterations.
- Planner failure or no initial tool calls triggers a conservative fallback search.
- Agentic final answer assembly can include up to two compiled search contexts, deduplicate repeated node records, and enforce a shared context budget.
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
- Saved exchanges update a session-scoped active document registry from answer `used_node_ids`.
- Active document records preserve document ID, title, source metadata, labels, referenced node IDs, and reference counts.
- Web and CLI answer calls expose structured process traces.
- Process traces include prompt intake, memory-agent iterations, tool calls, retrieval/context assembly, and answer adapter execution.
- `.restart.md` is maintained manually as a working restart note; it is still not the required graph-backed restart state source of truth.

## Inferred Requirements

These are not yet fully formalised in the original documents, but the implementation has shown they are needed.

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
- fallback probes try exact phrases first, then near-match candidates, then original single terms;
- process traces and prompts expose near matches as diagnostics.

Recommended next adaptation:

1. Do not port `jscompare` directly into production retrieval.
2. Keep the Python helper behind the query assembly layer.
3. Use standard-library or maintained Python options before adding a new dependency.
4. Extend near-match vocabulary gradually to active documents and other bounded title/source candidates.
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
- active document registry beyond the current used-node capture skeleton;
- graph-backed restart state node;
- natural-language endorsement writes;
- traversal scoring feedback;
- REM consolidation;
- output ingestion back into the memory graph;
- source-document fallback after retrieval failure;
- optional web search queued for ingestion.

## Current Project Status Snapshot

As of 2026-05-21:

- Git branch: `main`.
- Latest synced commit before this addendum: `9dae48f Improve natural-language node search`.
- Working tree before this addendum: clean.
- Mongo database: `mnemosyne_dev`.
- Mongo counts at status check:
  - documents: 1877;
  - trees: 1877;
  - nodes: 176428;
  - exchanges: 70;
  - sessions: 15;
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
- Current automated suite at last full run: `126 passed`.

## Recommended Next Work

Near-term consolidation:

1. Update roadmap/status docs to reference this addendum.
2. Decide whether deterministic fuzzy lexical expansion should be added before embeddings.
3. If yes, add fuzzy expansion inside query assembly with diagnostics and tests.
4. Keep retrieval improvements framed as scaffold support until graph traversal and semantic map work exist.

Next implementation candidates:

1. Add a confidence threshold for deciding when weak partial matches should still trigger near-match fallback.
2. Broaden near-match vocabulary carefully, next with active documents.
3. Add source-document fallback when Mongo node retrieval fails.
4. Add active document registry skeleton.
5. Add graph edge schema and first typed relation writes.
6. Add embedding interface and a brute-force baseline evaluation harness.
