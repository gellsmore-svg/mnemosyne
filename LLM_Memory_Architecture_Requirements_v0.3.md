# LLM Memory Architecture — Requirements Document
**Version:** 0.3 (Working Draft)
**Status:** For Review
**Date:** May 2026
**Changes from v0.2:** Three-tier provenance model introduced (document of interest / implicit endorsement / explicit endorsement); ingestion failure handling added with transactional commit, retry queue, error log, and dead letter folder; traversal scoring defined with chunk-level and path-level scoring; quantitative query mechanism added to retrieval; web interface moved to Stage 2 and tabbed session interface specified; semantic map given dedicated build stage callout; cold start fallback requirement added; section numbering corrected.

---

## 1. Purpose and Context

Current LLM memory solutions are either brute-force (dump all documents into the context window, consuming tokens before any useful work begins) or opaque (delegate memory decisions entirely to an agent without human oversight or structural discipline). This system aims to replace both approaches with a structured, navigable, graph-based memory layer that:

- Operates locally, using Gemma as the primary model
- Reduces token waste in the reasoning context window
- Improves retrieval quality over time as the corpus grows
- Maintains clear provenance and trust hierarchies throughout
- Becomes measurably more effective than brute force once the corpus exceeds context window capacity
- Preserves continuity of thought, process, and session across context window boundaries and across sessions

---

## 2. System Overview

The system consists of five layers:

1. **Ingestion Layer** — documents are parsed, chunked, and stored as structured trees (Phase 1). Each document may produce one or more trees, each representing a distinct contextual view of that document. The relationship between a document and its trees is one-to-many.
2. **Consolidation Layer** — async background process correlates across documents and trees, maintains the semantic map (Phase 2 / REM)
3. **Retrieval Layer** — Gemma navigates the graph to compile an optimised context document
4. **Reasoning Layer** — the compiled context document is passed to the thinking LLM (Gemma or external)
5. **Session Continuity Layer** — maintains thought, process, and session continuity across context window boundaries and across sessions

---

## 3. Functional Requirements

### 3.1 Ingestion — Phase 1 (Immediate, Per-Document)

**REQ-ING-01:** The system shall accept documents in any text-based format (markdown, plain text, code files, web-fetched content) via a watched ingestion folder.

**REQ-ING-02:** A cron job or equivalent process shall detect new files in the ingestion folder, trigger ingestion, and move processed files to an archive location on successful completion. Files that fail ingestion shall not be moved to the archive.

**REQ-ING-03:** Gemma shall determine the chunking strategy for each document. Chunks may be phrases, sentences, multi-sentence clusters, code blocks, or other structural units as determined by document type and content. No fixed rule-based pre-pass is required.

**REQ-ING-04:** For each chunk, Gemma shall assess the relevance of adjacent chunks (previous and next structural unit) and assign a proximity relevance score. This score determines how far retrieval should expand around any given chunk during navigation.

**REQ-ING-05:** Gemma shall identify relationships between chunks within the document and label edges with a relationship type and a confidence score (numeric, 0.0–10.0).

**REQ-ING-06:** Each document shall produce one or more trees. Each tree represents a distinct contextual view of the document. Gemma shall infer a context label for each tree individually. Different trees derived from the same document may carry different Gemma-inferred context labels.

**REQ-ING-07:** The user may optionally declare a context label at the document level at ingestion time. Where a user-declared context label exists, it shall take precedence over Gemma-inferred context labels across all trees derived from that document. Both the user-declared label and the per-tree Gemma-inferred labels shall be stored.

**REQ-ING-08:** Every chunk node shall carry a provenance tier reflecting the level of human endorsement. The three provenance tiers are defined in Section 6. Provenance is assigned at chunk level, not document level, except where implicit endorsement is declared at ingestion time, in which case all chunks from that document are marked accordingly on creation.

**REQ-ING-09:** Every chunk node shall carry a reference to its source document, including document ID, version, and storage location, so that retrieval can escalate to the full document if needed.

**REQ-ING-10:** Ingestion shall be transactional. No data shall be written to the active MongoDB corpus until Gemma has confirmed the ingestion of the entire document is complete and coherent. Partial ingestion results shall be held in a temporary staging area and discarded on failure.

**REQ-ING-11:** The resulting tree structures shall be stored in MongoDB as graphs of nodes with typed, weighted edges (adjacency list model). A dedicated graph database is not required.

**REQ-ING-12:** When a new version of a document is ingested, new trees shall be created. The old and new trees shall be linked by versioning edges: **supersedes** (new → old) and **superseded-by** (old → new). Old trees shall not be deleted.

---

### 3.2 Ingestion Failure Handling

**REQ-FAI-01:** The ingestion process shall define and handle failure at three distinct points:

- **Parsing failure** — the document cannot be read or its text cannot be extracted and passed to Gemma. Causes include unreadable encoding, unsupported format, or corrupted file.
- **Processing failure** — Gemma receives the document content but determines it cannot be meaningfully ingested. Causes include content that does not render as coherent human-readable or machine-readable text, or content that cannot be chunked and labelled with sufficient confidence.
- **Database write failure** — the ingestion staging data cannot be committed to MongoDB. Causes include connection failure, schema violation, or transaction timeout.

**REQ-FAI-02:** On any failure, the document shall be placed in a retry queue. The system shall attempt re-ingestion a configurable number of times (default: three) before declaring the document permanently failed.

**REQ-FAI-03:** Each failure attempt shall be recorded in an error log, including: document identifier, failure point (parsing / processing / database), error description, timestamp, and attempt number.

**REQ-FAI-04:** Documents that exhaust all retry attempts shall be moved to a dead letter folder — a designated location for permanently failed documents that can be inspected and resubmitted manually without being lost. The dead letter folder is distinct from both the ingestion folder and the archive.

**REQ-FAI-05:** No partial ingestion data shall persist in the active MongoDB corpus after a failure. The staging area used during ingestion shall be cleared on failure before retry.

---

### 3.3 Consolidation — Phase 2 (Async / REM Process)

**REQ-CON-01:** A background consolidation process shall run on a scheduled basis (e.g. nightly) independent of live ingestion and retrieval operations.

**REQ-CON-02:** The consolidation process shall build cross-document edges between nodes from different documents and trees that share semantic relationships. These edges shall be typed and confidence-scored in the same manner as intra-document edges.

**REQ-CON-03:** The consolidation process shall maintain a **semantic map** — a separate structure that clusters relationship labels by meaning and context. Labels that are near-synonymous within the same context shall be grouped. Labels that have multiple discrete meanings across different contexts shall be forked into separate sense clusters.

**REQ-CON-04:** Each sense cluster in the semantic map shall carry: member labels, context qualifier (user-declared or Gemma-inferred), and an aggregate confidence score derived from the constituent edge scores.

**REQ-CON-05:** The semantic map shall be versioned. Periodic snapshots shall be stored so that the evolution of the label vocabulary can be traversed historically.

**REQ-CON-06:** Low-confidence edges from Phase 1 (below a configurable threshold) shall be flagged as consolidation candidates and prioritised for review during the REM process.

**REQ-CON-07:** The consolidation process shall itself be orchestrated by Gemma, which may use lighter embedding-based similarity checks to identify candidate clusters before applying full reasoning to confirm or reject merges.

---

### 3.4 Semantic Map — Label Structure

**REQ-SEM-01:** The semantic map shall support polysemous labels — labels that carry more than one discrete meaning in different contexts. Each meaning shall be represented as a separate sense node with its own cluster of related terms.

**REQ-SEM-02:** Each sense node shall carry a set of associated terms (synonyms and related terms), each with a weighted relationship score indicating strength of association.

**REQ-SEM-03:** Relationship scores between associated terms and their sense nodes shall be adjusted over time based on retrieval feedback: successful use of a relationship path increases its score; rejection or poor outcome decreases it.

**REQ-SEM-04:** The semantic map shall serve as a synonym lookup during query expansion, enabling retrieval to find nodes regardless of which specific label variant was used at ingestion time.

---

### 3.5 Retrieval — Navigable Graph Traversal

**REQ-RET-01:** Retrieval shall be navigable, not single-query. Gemma shall traverse the graph iteratively, following weighted edges, expanding context where proximity scores indicate adjacent chunks are relevant, and deciding when sufficient context has been assembled.

**REQ-RET-02:** Gemma shall generate its own retrieval queries based on the current task. It shall assess whether the result is sufficient and re-query or expand traversal if not.

**REQ-RET-03:** Where the graph returns no useful results, Gemma shall fall back to direct reading of the source document via the document reference stored on each node, or initiate a web search if no relevant document is identified.

**REQ-RET-04:** Where retrieval from the graph and source documents is insufficient, Gemma may initiate a web search. Content found via web search shall be queued for ingestion so that it becomes part of the persistent knowledge graph.

**REQ-RET-05:** During retrieval, user-declared context labels shall be applied as a primary filter where specified. If no user context is declared, both user-declared and Gemma-inferred context labels shall be considered.

**REQ-RET-06:** Provenance tiers shall be surfaced during retrieval. Gemma shall weight explicitly human-endorsed content with the highest authority, implicitly endorsed content with moderate authority, and documents of interest as reference material pending endorsement.

**REQ-RET-07:** The retrieval mechanism shall support two query modes operating in combination:

- **Qualitative mode** — semantic query based on meaning, context labels, and edge traversal
- **Quantitative mode** — direct lookup of specific chunks or documents by identifier, for use when Gemma knows a specific document or chunk is relevant and wishes to retrieve it directly without semantic traversal

**REQ-RET-08:** Traversal scoring shall operate as follows: when a traversal path is followed and one or more chunks from that path are used in the compiled context document, the traversal path score and each used chunk's score shall each increase by one unit. When a traversal path is followed and no chunks from that path are used, the traversal path score shall decrease by one unit. Chunk scores shall never decrease.

**REQ-RET-09:** Traversal scores shall be used for cluster-based prioritisation. Where a traversal path has a high score and leads to a document present on the current session's active document list, but that path has not been returned by the current query, Gemma shall receive it as an optional recommendation to explore for context continuity. Gemma is not required to follow this recommendation but shall be made aware of it.

---

### 3.6 Context Document Compilation

**REQ-CTX-01:** Gemma shall compile the results of retrieval into an optimised context document for passing to the reasoning LLM.

**REQ-CTX-02:** The compiled context document shall include, for each result cluster: source document reference, provenance tier, chunk content or summary, relationship metadata, confidence scores, and an indication of whether adjacent or full-document context is available.

**REQ-CTX-03:** Gemma shall assess the compiled context document for completeness and coherence before passing it to the reasoning LLM. If insufficient, Gemma shall conduct further retrieval rather than passing an incomplete result.

**REQ-CTX-04:** The compiled context document format shall be optimised for LLM consumption. Structured metadata may be included alongside or instead of prose reconstruction.

**REQ-CTX-05:** Every reasoning LLM call shall include a standard system instruction requiring the LLM to evaluate its own confidence level across the request and surface any questions it deems relevant before or alongside its response.

---

### 3.7 Output Ingestion

**REQ-OUT-01:** Any text-based output produced by the reasoning LLM (files, documents, structured responses) shall be queued for ingestion back into the knowledge graph.

**REQ-OUT-02:** All LLM-generated outputs ingested via this route shall be assigned a provenance tier of **document of interest** by default. They shall not be elevated to implicitly or explicitly endorsed without deliberate user action.

---

### 3.8 Provenance Endorsement Mechanism

**REQ-END-01:** The system shall support a natural language endorsement pathway. When a user expresses approval of content conversationally, the active LLM shall interpret this as a potential explicit endorsement signal and act accordingly.

**REQ-END-02:** Endorsement operates at chunk level. Endorsing a document endorses all of its chunks only when the user explicitly states full approval of the entire document at ingestion time (implicit endorsement). All other endorsements target specific chunks identified through conversation.

**REQ-END-03:** On detecting a potential endorsement signal, the active LLM shall identify the most likely target chunks by cross-referencing the active document registry and the current session context. Where the target is unambiguous, it shall mark the relevant chunks as explicitly human-endorsed in the database. Where ambiguous, it shall ask a clarifying question before acting.

**REQ-END-04:** Where a user references content from a previous session, the LLM shall query the database to identify the most likely candidate chunks, present its interpretation to the user for confirmation, and only endorse on confirmation.

**REQ-END-05:** Endorsement shall be a first-class database operation, updating the provenance tier on the relevant chunk nodes and recording the endorsing session ID and timestamp.

---

### 3.9 Active Document Registry

**REQ-ADR-01:** The database shall maintain an active document registry for each session thread, listing all documents referenced, produced, or ingested during that session, with links to their stored originals and their current provenance tier.

**REQ-ADR-02:** The active document registry shall be queryable by the LLM during endorsement operations, continuity checks, and retrieval, so that document references in natural language can be resolved to specific database entries.

**REQ-ADR-03:** Where the LLM cannot resolve a natural language document reference directly, it may read the original document via the stored link to perform the correlation.

---

### 3.10 Session Continuity

**REQ-SCO-01:** The system shall maintain three distinct types of continuity across context window boundaries and across sessions:

- **Thought continuity** — active reasoning chains, hypotheses under exploration, pending decisions
- **Process continuity** — working environment facts, tool availability, access patterns, workflow decisions, architectural decisions made and their rationale
- **Session continuity** — chat history, prompt and response pairs, session linkages, goals and current state

**REQ-SCO-02:** Each chat session shall be assigned a unique session ID. Prompts and responses shall be chunked and ingested into the knowledge graph tagged with their session ID and a session context label. The same retrieval mechanisms used for documents shall apply to session content.

**REQ-SCO-03:** Sessions may be linked to form a session cluster representing a larger project or ongoing thread. Linked sessions shall share a project ID in addition to their individual session IDs.

**REQ-SCO-04:** Continuity-critical items shall be flaggable across all context label types. A continuity-critical flag indicates that the item must survive context window compression and be carried forward into subsequent prompts within the session. Examples of continuity-critical items include: settled decisions and their rationale, discovered constraints, failed approaches and why they were abandoned, and explicit user preferences or working style decisions.

**REQ-SCO-05:** On every prompt, the active LLM shall check for any continuity-critical items relevant to the current session and ensure they are present in the context document passed to the reasoning LLM.

**REQ-SCO-06:** The system shall maintain a restart state node in the graph, updated after every exchange, representing the minimum information required to resume any active session thread. This node shall be the source of truth for session restart.

**REQ-SCO-07:** The system shall maintain a restart file (e.g. `.restart.md`) as a human-readable rendered view of the restart state node. This file shall list all active session threads with a brief restart instruction for each, and shall prompt the user on opening to select a thread to continue or start a new one.

---

### 3.11 Context Label Types

The system shall support the following context label types, applicable to any node in the graph:

| Label Type | Description |
|---|---|
| **Document context** | The subject domain of the content — e.g. legal, commercial, technical, philosophical |
| **Process context** | Working environment facts, tool availability, workflow and architectural decisions |
| **Thought context** | Active reasoning chains, hypotheses, pending decisions |
| **Session context** | Chat history, prompt/response pairs, session linkages |
| **Environmental context** | System state, folder structures, running processes relevant to current work |

Continuity-critical is a **flag**, not a label type, and may be applied to nodes of any label type.

---

## 4. Interface Requirements

### 4.1 Web Interface

**REQ-UI-01:** The system shall provide a local web interface (localhost) as the primary human interaction point for testing and use during development.

**REQ-UI-02:** The interface shall present a simple conversational prompt-and-response layout. The user types a prompt, submits it, and receives a response. No direct database interaction shall be exposed to the user; all database operations shall be mediated by Gemma or the active LLM.

**REQ-UI-03:** The interface shall be structurally abstracted so that it can be deployed as a web application, desktop application, or integrated into other environments without changes to the underlying system.

**REQ-UI-04:** Document ingestion shall be supported via the file system (watched ingestion folder) rather than requiring upload through the web interface at this stage. The interface need not include a file upload mechanism in the initial implementation.

**REQ-UI-05:** The interface shall display active session threads as tabs. Each tab shall be labelled by Gemma with an intelligently generated short title reflecting the thread content. Selecting a tab shall implicitly set that thread as the active session context for all subsequent prompts and responses.

**REQ-UI-06:** A new session thread shall be startable from the interface without requiring a system restart.

---

## 5. Non-Functional Requirements

**REQ-NFR-01 — Local Operation:** The system shall run entirely on local hardware. No mandatory dependency on cloud LLM APIs. External LLM calls (Claude, ChatGPT, Grok, etc.) shall be optional and invoked only when explicitly requested for the reasoning step.

**REQ-NFR-02 — Asynchronous Ingestion:** Phase 1 ingestion and Phase 2 consolidation shall not block reasoning or interface operations. Ingestion runs as a background process.

**REQ-NFR-03 — Hardware Baseline:** The initial implementation shall target a machine with 32GB RAM and an NVIDIA GTX 3060 mobile GPU. Gemma model size selection shall be constrained accordingly.

**REQ-NFR-04 — Storage:** MongoDB shall be the primary storage layer. The graph shall be modelled as an adjacency list within MongoDB documents. A dedicated graph database is not required.

**REQ-NFR-05 — Measurable Improvement:** The system shall be evaluable against brute-force retrieval. Evaluation shall compare: intelligibility of output, token bloat ratio, coverage of relevant information, and coherence. Evaluation shall be conducted by a separate Gemma instance operating in an isolated context window.

**REQ-NFR-06 — Cold Start Acknowledgement:** The system shall be expected to underperform brute-force retrieval during the cold start period (sparse corpus). Performance parity and eventual superiority are expected once the corpus exceeds the effective context window capacity of the target reasoning LLM. This is by design, not a defect.

---

## 6. Trust and Provenance Model

The system uses a three-tier provenance model. Provenance is assigned and tracked at chunk level.

| Tier | Name | Description | Default For |
|---|---|---|---|
| 1 | **Document of interest** | Content the user has made available for the system to consider. Not authoritative. Chunks are not human-endorsed. The thinking LLM may review and surface relevant elements for user consideration. | All ingested documents unless otherwise declared; all LLM-generated outputs |
| 2 | **Implicitly endorsed** | The user has explicitly stated at ingestion time that they fully approve the entire document. All chunks from that document are marked implicitly endorsed on creation. | Documents where user declares full approval at ingestion |
| 3 | **Explicitly endorsed** | The user has endorsed specific content during a conversation via the natural language endorsement mechanism. Only these specific chunks carry full human authority. | Chunks endorsed through conversation |

Explicitly endorsed content (Tier 3) shall take the highest precedence in retrieval ranking. Implicitly endorsed content (Tier 2) shall take moderate precedence. Documents of interest (Tier 1) shall be treated as reference material.

Human-authored session prompts shall be treated as Tier 3 by default, as they represent direct human intent.

---

## 7. Build Stages (Recommended)

**Stage 1 — Phase 1 Ingestion (Single Document)**
Prove the core ingestion loop: Gemma reads a document, produces chunks across one or more trees, labels edges with confidence scores, writes trees to MongoDB transactionally. Implement the retry queue, error log, and dead letter folder. Validate the data schema holds under real documents before proceeding.

**Stage 2 — Web Interface (Basic)**
Implement the local web interface with tabbed session thread display, prompt/response loop, and new thread creation. This provides a usable testing surface for all subsequent stages and makes progress visible and tangible.

**Stage 3 — Retrieval and Context Compilation**
Implement navigable graph traversal in both qualitative and quantitative modes, context document compilation, and cold start fallback to direct document reading. Pass compiled context to a reasoning LLM and measure output quality against a brute-force baseline.

**Stage 4 — Phase 2 Consolidation and Semantic Map**
Add the REM process: cross-document and cross-tree edge building. Implement the semantic map with sense cluster generation, polysemy support, synonym lookup, and versioned snapshots.

**Stage 5 — Session Continuity and Endorsement**
Implement session chunking and ingestion, the restart file and restart state node, continuity-critical flagging, the active document registry, and the three-tier natural language endorsement mechanism.

**Stage 6 — Traversal Scoring and Feedback**
Implement path-level and chunk-level scoring, cluster-based prioritisation, and the optional context continuity recommendation mechanism.

---

## 8. Design Questions (For Design Document)

The following questions are deferred to the design document:

- **DQ-01:** What is the minimum viable MongoDB schema for chunk nodes, edges, the semantic map, and the active document registry?
- **DQ-02:** What is the exact format of the compiled context document passed to the reasoning LLM — structured JSON, annotated prose, or hybrid?
- **DQ-03:** How is the traversal scoring signal generated in practice — does Gemma emit an explicit signal after compilation, or is chunk usage tracked mechanically during the compilation step?
- **DQ-04:** What is the configurable confidence threshold below which Phase 1 edges are flagged for consolidation review?
- **DQ-05:** How are ingestion conflicts handled where two documents assert contradictory information? Should contradiction be a first-class edge type?
- **DQ-06:** What is the Gemma model selection for each role (ingestion, consolidation, retrieval, reasoning) given the hardware constraints, and should different model sizes be used for different roles?

---

*End of Requirements Document v0.3*
