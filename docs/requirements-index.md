# Requirements Index

Last updated: 2026-05-17

This file is a compact implementation index for `LLM_Memory_Architecture_Requirements_v0.3.md`.

## Ingestion

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| Watched ingestion folder | REQ-ING-01, REQ-ING-02 | Polling is acceptable; archive only after successful commit. |
| Gemma chunking | REQ-ING-03 to REQ-ING-06 | Gemma chooses chunks, proximity, relationships, and one-or-more trees. |
| Context labels | REQ-ING-06, REQ-ING-07 | User-declared document label overrides effective context across derived trees; keep Gemma labels too. |
| Graph edges | REQ-ING-04, REQ-ING-05, REQ-ING-11 | First `graph_edges` collection persists ingestion relation hints when they reference known node keys; `get_graph_edges` exposes bounded single-hop lookup and `expand_proximity` ranks one-hop neighbors. Scored multi-hop traversal and relation inference remain deferred. |
| Provenance | REQ-ING-08, Section 6 | Chunk-level, three tiers. |
| Source references | REQ-ING-09 | Nodes must link to document ID, version, and storage path. |
| Transactional ingestion | REQ-ING-10 to REQ-ING-12 | Stage first; commit only after complete coherent Gemma processing. |

## Failure Handling

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| Failure types | REQ-FAI-01 | Parsing, processing, database. |
| Retry queue | REQ-FAI-02 | Default three attempts. |
| Error logging | REQ-FAI-03 | Include document ID, point, description, timestamp, attempt. |
| Dead letter folder | REQ-FAI-04 | Distinct from ingestion and archive. |
| No partial active corpus writes | REQ-FAI-05 | Clear staging on failure. |

## Consolidation And Semantic Map

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| REM process | REQ-CON-01, REQ-CON-02 | Scheduled background process. |
| Semantic map | REQ-CON-03 to REQ-CON-05, REQ-SEM-01 to REQ-SEM-04 | Sense clusters, polysemy support, snapshots, synonym expansion. |
| Low-confidence edge review | REQ-CON-06 | Default threshold in design is 5.0. |
| Embedding pre-clustering | REQ-CON-07 | Gemma confirms candidate clusters after embedding search. |

## Retrieval And Context Compilation

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| Navigable retrieval | REQ-RET-01, REQ-RET-02 | Iterative traversal, not one-shot search. |
| Fallbacks | REQ-RET-03, REQ-RET-04 | Direct document read, then optional web search queued for ingestion. |
| Context filtering | REQ-RET-05, REQ-RET-06 | Context labels and provenance tiers influence ranking. |
| Query modes | REQ-RET-07 | Qualitative semantic and quantitative direct lookup. |
| Traversal scoring | REQ-RET-08, REQ-RET-09 | Path and chunk usage feedback; chunk score never decreases. |
| Context document | REQ-CTX-01 to REQ-CTX-05 | Structured context plus self-confidence/questioning instruction. |

## Session Continuity And Endorsement

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| Continuity types | REQ-SCO-01 | Thought, process, session. |
| Session ingestion | REQ-SCO-02, REQ-SCO-03 | Session IDs and project/session clusters. |
| Continuity-critical flag | REQ-SCO-04, REQ-SCO-05 | Flag, not label type; must survive compression. |
| Restart state | REQ-SCO-06, REQ-SCO-07 | Graph node is source of truth; `.restart.md` is rendered view. |
| Endorsement | REQ-END-01 to REQ-END-05 | Natural language endorsement, chunk-level writes, clarify if ambiguous. |
| Active document registry | REQ-ADR-01 to REQ-ADR-03 | Needed for endorsement and retrieval resolution. |

## Interface And Non-Functional

| Area | Requirement IDs | Implementation Notes |
|---|---|---|
| Local web UI | REQ-UI-01 to REQ-UI-06 | Stage 2; tabbed session threads. |
| Practical integrations | Inferred application requirement | See `docs/practical-applications.md`; Mnemosyne should expose memory APIs usable by web importers, coding agents, CLI workflows, voice transcript tools, and FOSS clients. |
| Local operation | REQ-NFR-01 | Cloud APIs optional only. |
| Async pipelines | REQ-NFR-02 | Ingestion/consolidation do not block UI. |
| Hardware baseline | REQ-NFR-03 | 32GB RAM target, GTX 3060 mobile, 8GB initial constraint noted. |
| Storage | REQ-NFR-04 | MongoDB adjacency-list graph. |
| Evaluation | REQ-NFR-05, REQ-NFR-06 | Compare to brute force; expect cold-start underperformance. |
