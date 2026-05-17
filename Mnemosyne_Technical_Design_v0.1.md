# Mnemosyne — Technical Design Document
**Version:** 0.1 (Working Draft)
**Status:** For Review
**Date:** May 2026
**Companion Document:** LLM Memory Architecture — Requirements Document v0.3

---

## 1. System Name and Concept

**Mnemosyne** (from the Greek goddess of memory, mother of the Muses) is a locally-operated, graph-based memory and retrieval layer for LLM interactions. It sits between the user and any reasoning LLM, providing structured, navigable, provenance-aware context rather than brute-force document dumping.

---

## 2. Technology Stack

### 2.1 Core Language
**Python 3.11+**
Rationale: The LLM tooling ecosystem (Hugging Face Transformers, llama-cpp-python, LangChain primitives, pymongo) is Python-native. All orchestration, ingestion, consolidation, retrieval, and API layers will be Python.

### 2.2 Local LLM Runtime
**Hugging Face Transformers + llama-cpp-python**
Rationale: Provides direct model access without an intermediary like Ollama. Transformers handles model loading and inference for Gemma. llama-cpp-python provides GGUF quantized model support for memory-constrained operation. Both libraries expose the model directly, giving Mnemosyne full control over the inference call, system prompt, and response handling.

### 2.3 LLM Model Adapter
All calls to any LLM within Mnemosyne shall pass through a model adapter interface. The adapter abstracts the underlying model and runtime so that Gemma can be replaced with any other model (Mistral, LLaMA, cloud API) by changing configuration, not code.

```
MnemosyneAdapter (abstract interface)
    └── GemmaAdapter (concrete implementation, initial)
    └── [Future: MistralAdapter, ClaudeAdapter, OpenAIAdapter, etc.]
```

The adapter interface shall expose the following methods:
- `generate(system_prompt, user_prompt, max_tokens) → str`
- `generate_structured(system_prompt, user_prompt, schema) → dict`
- `embed(text) → list[float]`

### 2.4 Primary Model Selection
**Gemma 3 12B (Q4 quantized)** — recommended for ingestion, consolidation, and reasoning roles once 32GB RAM is available.
**Gemma 3 4B (Q4 quantized)** — recommended as fallback for embedding and lightweight classification tasks, or during development on 8GB RAM.

Rationale: The 3060 mobile has 6GB VRAM. With 32GB system RAM, the 12B Q4 model (~7GB) can be partially offloaded to CPU with acceptable inference speed for background tasks. The 4B model fits comfortably for lighter operations. Model selection per role is configurable, not hardcoded.

### 2.5 Database
**MongoDB 7.x (local instance)**
Rationale: Document model fits the graph adjacency list structure naturally. Flexible schema accommodates the evolving node and edge structure during development. Atlas Search provides full-text and vector search without a separate search engine. No cloud dependency.

### 2.6 Message Queue
**MongoDB-backed queue (custom lightweight implementation)**
Rationale: A dedicated message broker (Kafka, RabbitMQ) is architecturally appropriate for production scale but is overkill for the prototype. A MongoDB collection used as a queue provides sufficient reliability and persistence for the ingestion and consolidation pipelines while keeping the stack simple. The queue interface shall be abstracted behind a `QueueAdapter` so that Kafka or another broker can be substituted later with no functional impact on the pipeline code.

```
QueueAdapter (abstract interface)
    └── MongoQueueAdapter (concrete implementation, initial)
    └── [Future: KafkaAdapter, etc.]
```

### 2.7 Embeddings
**sentence-transformers (all-MiniLM-L6-v2 or equivalent)**
Rationale: Lightweight embedding model for the pre-clustering step in Phase 2 consolidation. Runs entirely on CPU. Does not require GPU allocation. Kept separate from the main Gemma inference pipeline so both can operate concurrently.

### 2.8 Web Interface
**FastAPI (backend) + vanilla HTML/CSS/JavaScript (frontend)**
Rationale: FastAPI provides a clean async Python API layer with minimal boilerplate. Vanilla frontend avoids framework complexity at this stage — the interface is intentionally simple. WebSockets via FastAPI will handle streaming responses so the user sees output as it is generated rather than waiting for completion.

### 2.9 Task Scheduling
**APScheduler (Python)**
Rationale: Lightweight in-process scheduler for the Phase 2 REM consolidation process and the ingestion folder watcher. No separate daemon process required. Configurable schedules stored in system config.

### 2.10 Development Environment
- OS: Ubuntu 24 (or WSL2 on Windows)
- GPU: NVIDIA GTX 3060 mobile (6GB VRAM) — CUDA 12.x
- RAM: 32GB (development target); 8GB (initial constraint)
- Python environment: virtualenv per component

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Web Interface                       │
│         FastAPI + WebSocket + HTML/JS                │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Orchestration Layer                     │
│   Session management, prompt routing, endorsement   │
│              detection, queue dispatch               │
└───┬──────────────┬──────────────┬───────────────────┘
    │              │              │
┌───▼───┐    ┌─────▼─────┐  ┌────▼────────────────────┐
│Ingest │    │ Retrieval │  │  Consolidation (REM)    │
│Pipeline    │  Engine   │  │  Background Process     │
└───┬───┘    └─────┬─────┘  └────┬────────────────────┘
    │              │              │
┌───▼──────────────▼──────────────▼───────────────────┐
│                  MongoDB                             │
│  Documents · Trees · Nodes · Edges · Semantic Map   │
│  Sessions · Queue · Registry · Restart State        │
└─────────────────────────────────────────────────────┘
    │              │
┌───▼───┐    ┌─────▼──────┐
│ Model │    │ Embeddings │
│Adapter│    │  Service   │
└───────┘    └────────────┘
```

---

## 4. MongoDB Schema

All collections live in a single MongoDB database: `mnemosyne`.

### 4.1 Documents Collection (`documents`)

Represents a source document. One document record per physical file, regardless of how many trees are derived from it.

```json
{
  "_id": "ObjectId",
  "document_id": "uuid-string",
  "filename": "string",
  "file_path": "string",
  "file_hash": "string",
  "format": "markdown | plaintext | code | web",
  "ingested_at": "ISODate",
  "version": 1,
  "previous_version_id": "uuid-string | null",
  "user_declared_context": "string | null",
  "provenance_tier": 1,
  "implicit_endorsement": false,
  "status": "active | archived | superseded",
  "session_id": "uuid-string | null"
}
```

### 4.2 Trees Collection (`trees`)

One record per contextual tree derived from a document. A document may have many trees.

```json
{
  "_id": "ObjectId",
  "tree_id": "uuid-string",
  "document_id": "uuid-string",
  "gemma_inferred_context": "string",
  "effective_context": "string",
  "created_at": "ISODate",
  "version": 1,
  "supersedes_tree_id": "uuid-string | null",
  "superseded_by_tree_id": "uuid-string | null",
  "node_count": 42,
  "status": "active | superseded"
}
```

### 4.3 Nodes Collection (`nodes`)

One record per chunk. The core unit of the knowledge graph.

```json
{
  "_id": "ObjectId",
  "node_id": "uuid-string",
  "tree_id": "uuid-string",
  "document_id": "uuid-string",
  "content": "string",
  "summary": "string",
  "chunk_type": "phrase | sentence | cluster | code_block | heading | other",
  "position": 12,
  "provenance_tier": 1,
  "explicitly_endorsed_at": "ISODate | null",
  "endorsing_session_id": "uuid-string | null",
  "continuity_critical": false,
  "context_label_type": "document | process | thought | session | environmental",
  "usage_score": 0,
  "embedding": [0.023, -0.117, "..."],
  "relations": [
    {
      "target_node_id": "uuid-string",
      "relation_type": "string",
      "confidence": 8.4,
      "direction": "outbound | inbound | bidirectional",
      "cross_document": false,
      "traversal_score": 0
    }
  ],
  "proximity": {
    "prev_node_id": "uuid-string | null",
    "prev_relevance_score": 7.2,
    "next_node_id": "uuid-string | null",
    "next_relevance_score": 6.1
  },
  "created_at": "ISODate"
}
```

Note: Embeddings are stored on the node for vector similarity search during consolidation pre-clustering. MongoDB Atlas Search or a local vector index (MongoDB 7.x supports `$vectorSearch`) provides the query mechanism.

### 4.4 Semantic Map Collection (`semantic_map`)

One record per sense cluster. The semantic map is itself a graph of label meanings.

```json
{
  "_id": "ObjectId",
  "cluster_id": "uuid-string",
  "canonical_label": "string",
  "sense_description": "string",
  "context_qualifier": "string",
  "context_source": "user_declared | gemma_inferred",
  "member_labels": [
    {
      "label": "string",
      "association_score": 8.1
    }
  ],
  "related_clusters": [
    {
      "cluster_id": "uuid-string",
      "relation": "synonym | broader | narrower | distinct",
      "strength": 6.3
    }
  ],
  "aggregate_confidence": 8.4,
  "snapshot_version": 3,
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

### 4.5 Sessions Collection (`sessions`)

One record per chat session.

```json
{
  "_id": "ObjectId",
  "session_id": "uuid-string",
  "project_id": "uuid-string | null",
  "label": "string",
  "created_at": "ISODate",
  "last_active_at": "ISODate",
  "status": "active | archived",
  "linked_session_ids": ["uuid-string"],
  "restart_state": {
    "summary": "string",
    "continuity_critical_items": ["node_id"],
    "active_document_ids": ["uuid-string"],
    "last_updated_at": "ISODate"
  }
}
```

### 4.6 Exchanges Collection (`exchanges`)

One record per prompt/response pair within a session.

```json
{
  "_id": "ObjectId",
  "exchange_id": "uuid-string",
  "session_id": "uuid-string",
  "sequence": 1,
  "prompt": "string",
  "response": "string",
  "context_document_used": "string",
  "nodes_used": ["node_id"],
  "traversal_paths_used": ["edge_signature"],
  "created_at": "ISODate"
}
```

### 4.7 Active Document Registry Collection (`document_registry`)

One record per document per session — tracks which documents are active in which sessions.

```json
{
  "_id": "ObjectId",
  "session_id": "uuid-string",
  "document_id": "uuid-string",
  "filename": "string",
  "file_path": "string",
  "provenance_tier": 1,
  "added_at": "ISODate",
  "last_referenced_at": "ISODate"
}
```

### 4.8 Queue Collection (`ingestion_queue`)

MongoDB-backed queue for ingestion and consolidation jobs.

```json
{
  "_id": "ObjectId",
  "job_id": "uuid-string",
  "job_type": "ingest | consolidate | web_fetch",
  "payload": {
    "file_path": "string",
    "document_id": "uuid-string | null",
    "session_id": "uuid-string | null"
  },
  "status": "pending | processing | complete | failed | dead",
  "attempt_count": 0,
  "max_attempts": 3,
  "error_log": [
    {
      "attempt": 1,
      "failure_point": "parsing | processing | database",
      "error": "string",
      "timestamp": "ISODate"
    }
  ],
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

---

## 5. Ingestion Pipeline Design

### 5.1 Folder Watcher
APScheduler polls the ingestion folder every 60 seconds (configurable). On detecting a new file, it creates a job in `ingestion_queue` with status `pending` and leaves the file in place.

### 5.2 Ingestion Worker
A separate Python process (or APScheduler job) polls `ingestion_queue` for pending jobs. For each job:

**Step 1 — Parse**
Extract raw text from the file. For markdown and plaintext, direct read. For code, preserve structure. For web-fetched content, strip HTML. If extraction fails, record failure point `parsing` in the error log, increment attempt count, and re-queue if under max attempts. If max attempts exhausted, set status to `dead` and move file to dead letter folder.

**Step 2 — Stage**
Write raw text to a temporary staging document in MongoDB (`ingestion_staging` collection). Do not touch the active corpus at this point.

**Step 3 — Gemma Processing**
Call Gemma via the model adapter with the staged text. Gemma is prompted to:
- Determine chunking strategy and produce chunks with types and positions
- Identify relationships between chunks with labels and confidence scores
- Infer one or more contextual views of the document, each forming a tree
- Assign a context label to each tree
- Assess proximity relevance between adjacent chunks
- Generate a summary for each chunk
- Flag any content it cannot interpret as coherent text

Gemma returns a structured JSON response conforming to the ingestion schema. If Gemma flags content as uninterpretable or returns malformed JSON, record failure point `processing`, increment attempt, re-queue or dead-letter.

**Step 4 — Transactional Commit**
Only on Gemma confirming complete and coherent processing:
- Write the `documents` record
- Write all `trees` records
- Write all `nodes` records with embeddings (generated via the embeddings service)
- Clear the staging document

If any write fails, roll back all writes for this ingestion job, record failure point `database`, re-queue or dead-letter.

**Step 5 — Archive**
Move the source file from the ingestion folder to the archive folder. Update the job status to `complete`.

### 5.3 Ingestion Prompts
Gemma receives a structured system prompt that instructs it to return only valid JSON. The prompt includes:
- The document content
- The required output schema
- Instructions on chunking strategy, relationship labelling, confidence scoring, and context inference
- The user-declared context label if provided

### 5.4 Dead Letter Handling
Files in the dead letter folder are not processed automatically. The error log in `ingestion_queue` provides full failure history. The user may inspect the file, correct issues, and manually move it back to the ingestion folder to re-trigger ingestion.

---

## 6. Consolidation Process Design (REM)

### 6.1 Schedule
APScheduler triggers the REM process nightly at a configurable time (default: 02:00 local time). The process runs as a background task and does not block ingestion or retrieval.

### 6.2 REM Process Steps

**Step 1 — Identify candidates**
Query `nodes` for all nodes created or updated since the last consolidation run. Also query for nodes with low-confidence edges (below configurable threshold, default: 5.0).

**Step 2 — Embedding-based pre-clustering**
Run sentence-transformers embeddings across candidate node summaries. Use cosine similarity to identify clusters of semantically similar nodes across documents. This is a lightweight CPU operation that does not require Gemma.

**Step 3 — Gemma confirmation pass**
For each candidate cluster, pass the cluster members to Gemma with a prompt asking it to:
- Confirm whether the nodes are genuinely semantically related
- Propose cross-document edge labels and confidence scores
- Identify any polysemous labels that should be forked in the semantic map
- Propose sense cluster updates

**Step 4 — Cross-document edge writing**
Write confirmed cross-document edges to the relevant node records in `nodes`. Each cross-document edge is flagged `cross_document: true`.

**Step 5 — Semantic map update**
Update or create sense cluster records in `semantic_map`. Where a label is polysemous, fork into separate cluster records with distinct context qualifiers. Update `updated_at` on all modified clusters.

**Step 6 — Snapshot**
Increment `snapshot_version` on all modified semantic map records. Write a snapshot summary record to a `semantic_map_snapshots` collection with timestamp and change summary.

---

## 7. Retrieval Engine Design

### 7.1 Retrieval Trigger
The orchestration layer triggers retrieval on every incoming user prompt. It passes the prompt text and the current session ID to the retrieval engine.

### 7.2 Query Formation
Gemma receives the prompt and the current session context (continuity-critical items, active document list) and generates a retrieval query. The query specifies:
- Semantic search terms
- Context label filters
- Provenance tier preferences
- Any specific document or node IDs for quantitative lookup

### 7.3 Qualitative Query (Semantic)
MongoDB `$vectorSearch` against node embeddings, filtered by context label and provenance tier. Returns ranked candidate nodes. The semantic map is consulted to expand query terms with synonyms from the relevant sense clusters before the vector search runs.

### 7.4 Quantitative Query (Direct Lookup)
Direct MongoDB query by `node_id`, `document_id`, or `tree_id`. Used when Gemma knows a specific document or chunk is relevant. Returns the exact node or set of nodes without semantic ranking.

### 7.5 Navigation Loop
Gemma navigates the graph iteratively:

```
1. Run initial query (qualitative or quantitative)
2. Assess results — sufficient? → proceed to compilation
3. Not sufficient → follow high-confidence outbound edges from result nodes
4. Check proximity scores — expand to adjacent chunks if scores warrant
5. Check traversal score recommendations — any high-score paths to active documents not yet surfaced? → optional exploration
6. Repeat until Gemma determines context is sufficient or a configurable depth limit is reached
7. If graph exhausted → fall back to direct document read via document reference
8. If document read insufficient → queue web search job, proceed with available context
```

### 7.6 Cold Start Fallback
When the graph contains no nodes relevant to the query (empty graph or no matches above a minimum similarity threshold), the retrieval engine shall fall back to direct reading of any documents in the active document registry for the current session. If no documents are registered, it shall inform the orchestration layer that no stored context is available, and the reasoning LLM shall proceed on the prompt alone or initiate a web search.

### 7.7 Traversal Scoring
On completion of each retrieval cycle, the orchestration layer records which nodes and traversal paths were used in the compiled context document, as stored in the `exchanges` record. A post-exchange scoring job then:
- Increments `usage_score` by 1 on each used node
- Increments `traversal_score` by 1 on each edge in each used path
- Decrements `traversal_score` by 1 on each edge in traversed-but-unused paths

This job runs asynchronously after the exchange is recorded and does not delay the response.

### 7.8 Context Document Assembly
Gemma assembles the compiled context document as a structured JSON object:

```json
{
  "session_id": "uuid-string",
  "exchange_sequence": 4,
  "context_clusters": [
    {
      "cluster_id": 1,
      "source_document": "filename",
      "document_id": "uuid-string",
      "provenance_tier": 3,
      "chunks": [
        {
          "node_id": "uuid-string",
          "content": "string",
          "summary": "string",
          "chunk_type": "sentence",
          "confidence": 8.4,
          "usage_score": 12
        }
      ],
      "relationship_to_query": "string",
      "adjacent_context_available": true,
      "full_document_available": true
    }
  ],
  "continuity_critical_items": ["node_id"],
  "context_sufficiency": "sufficient | partial | insufficient",
  "recommended_paths": ["edge_signature"]
}
```

This JSON is serialised and prepended to the reasoning LLM's system prompt alongside the standard confidence and questioning instruction.

---

## 8. Session and Continuity Design

### 8.1 Session Initialisation
On first prompt in a new session:
- Generate a UUID session ID
- Create a `sessions` record with status `active`
- Gemma generates a short tab label based on the first prompt
- The session is added as a new tab in the web interface

### 8.2 Restart State Update
After every exchange, the orchestration layer prompts Gemma to update the restart state for the current session. Gemma produces:
- A short summary of the session state (current goal, decisions made, next steps)
- A list of continuity-critical node IDs relevant to the session
- The current active document list

This is written to the `restart_state` embedded document within the `sessions` record.

### 8.3 Restart File Rendering
After every restart state update, the system renders `.restart.md` by reading the restart state from all active sessions and producing:

```markdown
# Mnemosyne — Session Restart

Select a thread to continue, or start a new conversation.

---

## Thread 1: [Gemma-generated label]
**Last active:** [timestamp]
**Summary:** [restart state summary]
**To continue:** Load this thread in the interface.

---

## Thread 2: [Gemma-generated label]
...

---

*Start a new thread by opening the interface and selecting New Session.*
```

### 8.4 Continuity-Critical Flag Propagation
On every prompt, the orchestration layer queries for all nodes with `continuity_critical: true` linked to the current session ID. These are injected into the context document regardless of whether retrieval would otherwise surface them, up to a configurable token budget.

---

## 9. Endorsement Mechanism Design

### 9.1 Signal Detection
On every response generation, after the reasoning LLM produces its output, the orchestration layer passes the full exchange (prompt + response) to Gemma with a lightweight prompt asking: "Does this exchange contain a human endorsement signal? If so, what content is being endorsed?"

Gemma returns one of:
- `no_endorsement`
- `endorsement_detected` with a description of the endorsed content and confidence

### 9.2 Target Resolution
On detecting an endorsement signal, Gemma queries the active document registry and recent exchange nodes to identify the most likely target chunks. Where confidence is high (configurable threshold), it proceeds automatically. Where confidence is low, it generates a clarifying question to the user before acting.

### 9.3 Endorsement Write
On confirmed target resolution:
- Update `provenance_tier` to 3 on target chunk nodes
- Set `explicitly_endorsed_at` to current timestamp
- Set `endorsing_session_id` to current session ID
- Update the document registry entry to reflect the new tier

### 9.4 Implicit Endorsement at Ingestion
If the user declares full document approval at ingestion time (via a flag in the ingestion folder filename, e.g. `document.endorsed.md`, or via a system config option), all chunks produced from that document during Phase 1 ingestion are written with `provenance_tier: 2` from creation.

---

## 10. Web Interface Design

### 10.1 Stack
- **Backend:** FastAPI (Python), WebSocket support for streaming
- **Frontend:** Single HTML file, vanilla JavaScript, minimal CSS
- **No build step required** — served directly by FastAPI as a static file

### 10.2 Layout
```
┌─────────────────────────────────────────────────────┐
│  Mnemosyne    [Thread 1] [Thread 2] [+ New Thread]  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  [Response area — scrollable]                       │
│                                                     │
│                                                     │
├─────────────────────────────────────────────────────┤
│  [Prompt input — multiline]          [Send]         │
└─────────────────────────────────────────────────────┘
```

### 10.3 Tab Management
Each tab corresponds to a session record. Tab labels are Gemma-generated and stored in the `sessions` record. Clicking a tab sends a session switch event to the FastAPI backend, which updates the active session context for subsequent exchanges. Tabs persist across browser refresh (session state is in MongoDB, not browser storage).

### 10.4 Streaming Responses
FastAPI streams the reasoning LLM response token-by-token via WebSocket. The frontend appends tokens to the response area as they arrive. The send button is disabled during streaming and re-enabled on completion.

### 10.5 New Thread
Clicking `+ New Thread` sends a new session request to the backend, which creates a new session record and returns the session ID and an empty tab. The tab label is set to "New Thread" until Gemma generates a label after the first exchange.

### 10.6 Startup Behaviour
On loading the interface, the frontend requests the list of active sessions from the backend and renders a tab for each. The most recently active session is selected by default. If no sessions exist, a new session is created automatically.

---

## 11. Inter-Component Communication

### 11.1 Queue Design
All async jobs (ingestion, consolidation, web fetch, scoring) are submitted to the `ingestion_queue` collection in MongoDB. A single queue worker process polls the collection for pending jobs, processes them in order, and updates job status. Job types are routed to the appropriate handler by the worker.

The `QueueAdapter` interface means replacing this with Kafka later requires only a new concrete adapter implementation and a configuration change.

### 11.2 Component Boundaries

| Component | Communicates With | Via |
|---|---|---|
| Web Interface | Orchestration Layer | FastAPI HTTP + WebSocket |
| Orchestration Layer | Model Adapter | Direct Python call |
| Orchestration Layer | MongoDB | pymongo |
| Orchestration Layer | Queue | QueueAdapter |
| Ingestion Worker | Model Adapter | Direct Python call |
| Ingestion Worker | MongoDB | pymongo |
| Ingestion Worker | Embeddings Service | Direct Python call |
| Consolidation Worker | Model Adapter | Direct Python call |
| Consolidation Worker | MongoDB | pymongo |
| Consolidation Worker | Embeddings Service | Direct Python call |
| Retrieval Engine | Model Adapter | Direct Python call |
| Retrieval Engine | MongoDB | pymongo |

### 11.3 Configuration
All configurable parameters (model paths, MongoDB connection string, ingestion folder paths, queue poll intervals, REM schedule, confidence thresholds, retry limits) shall be stored in a single `config.yaml` file loaded at startup. No hardcoded configuration in application code.

---

## 12. Open Design Questions

- **DQ-03:** Traversal scoring signal — the current design records used nodes and paths in the `exchanges` record and scores them post-exchange. This should be validated in Stage 3 build to confirm the recording mechanism is reliable.
- **DQ-05:** Contradiction as a first-class edge type — not addressed in this version. Recommend adding `contradicts` as a valid relation type in the ingestion prompt schema and revisiting handling in a future design iteration.
- **DQ-04:** Confidence threshold for consolidation candidacy — default set to 5.0 in this document. Should be validated empirically during Stage 4 build and made configurable in `config.yaml`.

---

*End of Technical Design Document v0.1*
