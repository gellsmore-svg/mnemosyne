# Consolidated Requirements And Design

Date: 2026-05-29

Status: canonical working product/design document. This document consolidates the current Mnemosyne requirements, implemented design, inferred decisions, and open work into one review entry point. The older detailed documents remain useful supporting records, but this file should be the first place to look when deciding what to build next.

## Product Intent

Mnemosyne is a local-first memory engine for long-running LLM work. It preserves source material, builds document and semantic graph structures, retrieves evidence for questions, records continuity, and explains its own behavior in language a human can inspect.

Mnemosyne should not become a closed chatbot. It should become a memory backend that can serve:

- the current local web UI;
- CLI workflows;
- coding-agent support;
- web import and temporary internet context tools;
- voice transcript capture;
- future FOSS agent or knowledge-management clients.

The durable memory system owns source preservation, provenance, graph storage, retrieval, context construction, review state, and endorsement. External tools may help capture, transcribe, browse, or execute actions, but they should not bypass memory governance.

## Core Principles

### Source Authority

Source documents are the authority. Repository state should be disposable and reproducible from preserved sources plus reviewed generated artifacts.

The system must preserve source text and provenance. It must not silently rewrite, compress, summarize, or clean source material during ingestion unless that transformed material is explicitly stored as derived content.

### Memory As Cognitive Infrastructure

Memory is not passive storage queried by cognition. Memory should participate in:

- semantic weighting;
- contextual interpretation;
- procedural enforcement;
- identity-aware retrieval;
- trust evaluation;
- continuity of reasoning;
- collaboration and review.

The current implementation is still scaffolded, but future design should treat memory as active cognitive infrastructure rather than a flat RAG store.

### Transparency First

The user should be able to understand what happened without reading Python, raw JSON, or database records.

For every important flow, the system should expose:

- what was attempted;
- what information was used;
- which Python functions and LLM calls were involved;
- what decisions were made;
- what failed or was omitted;
- what the system saved for continuity.

JSON traces may remain available, but the default surface should read like a clear application log.

### Quality Before Speed

For the foreseeable future, quality is more important than performance. The system should prefer better interpretation, context construction, relationship extraction, provenance, and reviewability even when processing takes longer.

Optimization comes after the high-quality baseline is understood.

## User-Facing Requirements

### Ask Workspace

The Ask workspace is the primary experimentation surface. It must support real use, not just debugging.

Requirements:

- Prompt, Response, and Activity Log should be visible side by side on desktop.
- Prompt / Trace is supporting detail and should sit below the main row.
- The Ask button should live directly in or below the prompt panel.
- The activity log should start updating as soon as the request begins.
- The returned timeline should show each Python step and LLM handoff in order.
- LLM handoffs should expand to show the human-readable prompt/context package sent to the model.
- Raw JSON should be collapsed behind technical details.
- Low-intent prompts such as `hello` must not retrieve arbitrary corpus material.

### E-Paper Display Mode

The UI must support a Dasung-style 60Hz e-paper display mode.

Requirements:

- high-contrast light panels;
- no dark trace block;
- moderate text scale;
- proportional side-by-side columns;
- low visual texture;
- predictable layout at common desktop browser widths.

The current URL is:

```text
http://127.0.0.1:8765/?display=epaper
```

### Workspace Separation

Operational controls must not compete with question-answering.

The web UI should remain separated into:

- Ask: prompt, answer, activity log, trace.
- Browse: node search, documents, exchange history.
- Ingestion: source staging, inbox processing, semantic-edge review, ingest jobs.

## Answer And Retrieval Requirements

### Direct Retrieval

Direct retrieval should be conservative. It should search repository memory only when the prompt appears to be a substantive repository question, an active-document reference, or a focused-node request.

It currently supports:

- exact focus-node use;
- active-document scoping for references such as `this document`;
- lexical and near-match search;
- deterministic intent classification for empty, low-intent, active-document-reference, generic, and repository-query prompts;
- a minimum direct context match score before broad corpus search can select a node;
- a `controller_decision` trace object that explicitly marks direct retrieval as a deterministic scaffold and names the target owner as the memory-agent/controller;
- source-file fallback for active documents;
- no-context handling for low-intent conversational prompts.

Required next improvements:

- better explanation when retrieval is skipped;
- less dependence on lexical regex candidate collection.

### Agentic Retrieval

Agentic retrieval separates the memory-agent from the final answer model.

The memory-agent should not answer the user. It should iteratively gather memory by calling read-only Python tools, inspect observations, and stop when context is sufficient, clearly insufficient, or no useful read-only call remains.

Agentic answers emit the same `controller_decision` concept used by direct mode, but with `mode: agentic` and `current_owner: memory_agent_controller`. This keeps UI/reporting aligned with the target architecture while the deterministic direct path remains a scaffold.

Current allowed memory-agent tools:

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

Mnemosyne validates memory-agent tool calls, executes them through the Python runtime, records observations, and feeds compact summaries back into later planner iterations. If the LLM makes an invalid call, the tool layer returns an instructional error with usage guidance and a repair instruction so the next iteration can recover.

Failed tool-call guidance is preserved in memory-agent history and repeated in a dedicated repair-guidance section of the next planner prompt. The user-facing activity log also summarizes these failures in plain language so recovery is visible without reading the raw JSON trace.

When the memory-agent stops, it may return a bounded `context_proposal` containing selected node IDs, rationale, and organization hints. Mnemosyne treats this as a retrieval-controller proposal, not unchecked authority. The Python runtime validates node IDs, enforces budgets, ignores invented IDs, and uses the proposal only to prioritize matching context records.

### Query Assembly

Python builds a deterministic query assembly artifact used by both direct retrieval and agentic prompts.

Current fields include:

- lexical terms;
- exact phrases;
- named anchors;
- bounded near-match terms;
- fallback probe suggestions;
- active-document vocabulary hints.

This is the current bridge between deterministic retrieval, typo tolerance, UI diagnostics, and LLM planner guidance. It is not a substitute for embeddings, graph traversal, or semantic-map retrieval.

### Context Construction

Context construction must produce a bounded, inspectable package for the final answer model.

Current context package includes:

- rendered Markdown context for current local models;
- structured `context_document` metadata;
- included/skipped records;
- used node IDs;
- query diagnostics;
- selected tool outputs;
- context proposal metadata when present.

The final model should see source evidence before diagnostics. Diagnostics help the model understand retrieval behavior, but source content must remain primary.

## Ingestion Requirements

### Current Ingestion

Current ingestion is deterministic scaffold ingestion for Markdown and text files. It preserves source text, archives accepted files by checksum, rejects duplicates, writes documents/trees/nodes to MongoDB, and records queue/job state.

Current ingestion is not yet the target LLM-assisted semantic ingestion pipeline.

### Target Ingestion Pipeline

The target ingestion flow is:

```text
document
  -> source analysis
  -> metadata/date extraction
  -> semantic analysis
  -> chunk generation
  -> relationship analysis
  -> identity relevance analysis
  -> sensitivity analysis
  -> trust scoring
  -> temporal analysis
  -> process tagging
  -> graph placement
  -> semantic weighting
  -> storage
```

Every ingestion run should produce a human-readable activity log covering:

- source analysis;
- metadata and earliest credible date;
- concepts/entities detected;
- nodes created or updated;
- relationships detected or proposed;
- LLM calls made and why;
- repository writes;
- failures, retries, and review requirements.

### Repository Refresh

The repository should be treated as reproducible from source documents and reviewed generated artifacts.

Required operating model:

```text
Source Documents
  -> Fresh Ingestion
  -> Semantic Analysis
  -> Relationship Construction
  -> Inference Generation
  -> Repository Build
```

The system needs a controlled rebuild workflow that can:

- clear disposable generated repository content;
- re-ingest all source content;
- rebuild semantic relationships;
- rebuild inferred content;
- rebuild supporting node structures;
- tag runs with ingestion epochs;
- compare or supersede earlier generated content.

### Chronological Corpus Processing

Large historical corpora, especially AMS / Relational Substrate / RS5 Clause material, should be ingestible in likely historical order.

Earliest credible origin date priority:

1. Explicit date inside document content.
2. Date embedded in filename.
3. File creation date.
4. File modification date.

The earliest credible origin date should generally win, because the goal is to reconstruct the development of ideas over time.

## Graph, Semantics, And Review Requirements

### Graph Model

The graph must support:

- structural parent/child `contains` edges;
- reviewed semantic edges;
- conceptual relationships;
- causal relationships;
- contradiction relationships;
- reinforcement relationships;
- hierarchy relationships;
- dependency relationships;
- process relationships.

Relationship records should support:

- weight;
- confidence;
- trust;
- temporal relevance;
- provenance;
- identity visibility.

Current implementation includes structural edge backfill, read-only edge lookup, one-hop proximity expansion, bounded path expansion, semantic-candidate diagnostics, candidate queueing, and reviewed semantic-edge promotion.

Automated semantic relation extraction is still deferred.

### Generated Output Review

Saved LLM answers are queued as pending output-ingestion work. They can be processed into unreviewed generated-output nodes and later reviewed.

Generated output must not become trusted source memory automatically.

Review states include:

- unreviewed;
- implicit endorsed;
- explicit endorsed;
- rejected.

Future work should infer candidate endorsements and relationships from generated output, but writes must remain reviewable.

## Governance And Identity Requirements

### Identity Layers

Agents should have identity beyond prompt text.

Identity should define:

- semantic scope;
- trusted corpus;
- exclusions;
- behavioral expectations;
- process obligations;
- access permissions;
- weighting preferences;
- governance rules.

The target architecture includes:

- shared identity layer;
- domain identity layer;
- restricted identity layer.

Current implementation has read-only identity/governance records, seeded defaults, CLI/API listing and lookup, memory-agent identity prompt summaries, and agentic search exclusions.

### Process Enforcement

Processes should become enforceable semantic objects.

Target process objects should support:

- mandatory acknowledgement;
- execution tracking;
- procedural validation;
- step enforcement;
- escalation logic;
- audit trails;
- exception proposals;
- approval pathways.

Current process-run persistence is observational. Answer and ingestion flows create/update process runs where possible, but process rules are not yet enforced.

### Trust And Temporal Weighting

Trust and relevance should not be static.

Each semantic object should support:

- creation timestamp;
- last verified timestamp;
- last accessed timestamp;
- recency weighting;
- frequency weighting;
- confidence weighting;
- contextual persistence weighting.

Current trust/temporal diagnostics are explanatory and visible in retrieval traces. They do not yet affect retrieval ranking.

## Internet-Assisted Reasoning Requirements

Internet content may be used as temporary context when local memory is insufficient, but it must not automatically become durable memory.

Target workflow:

```text
User Question
  -> Initial Reasoning
  -> Determine Internet Needed
  -> Internet Retrieval
  -> Context Enrichment
  -> Additional LLM Pass
  -> Final Response
```

Promotion states:

- Temporary Context: used only for the current answer.
- Candidate Knowledge: stored separately for review.
- Permanent Knowledge: ingested only after review criteria are satisfied.

This protects the durable repository from unstable, low-quality, or unreviewed web material.

## Implemented Runtime Design

Current runtime shape:

- Python package under `src/mnemosyne`;
- local MongoDB persistence;
- FastAPI backend;
- static HTML/CSS/JS frontend;
- CLI commands for ingestion, retrieval, graph inspection, governance, sessions, process runs, and history;
- local answer adapters, including mock and Ollama CLI;
- direct and agentic retrieval modes;
- separate memory-agent model configuration;
- process traces, activity reports, and activity logs.

Current answer flow:

1. UI or CLI submits query, session, focus node, adapter/model, and retrieval mode.
2. Python records the prompt and starts an `answer_query` process run where possible.
3. Low-intent prompts are routed to no-context direct answering.
4. Direct or agentic retrieval gathers context.
5. Invalid memory-agent tool calls return usage and repair guidance.
6. Memory-agent may propose context ordering.
7. Python validates, budgets, and assembles final context.
8. Answer model receives the final prompt/context package.
9. Exchange is saved.
10. Used-node scoring, active-document tracking, and output-ingestion queueing run.
11. API returns answer, process trace, activity report, and plain-English activity log.

## Open Risks And Gaps

- Ingestion is still deterministic scaffold ingestion, not LLM-assisted semantic ingestion.
- Full repository refresh/rebuild is not yet a product workflow.
- Direct retrieval still needs explicit intent classification and relevance thresholds.
- Agentic retrieval is read-only and does not yet perform full semantic-map traversal.
- Context documents are scaffolded and do not yet match the full target schema.
- Trust/temporal diagnostics are explanatory only.
- Process runs are observational only; process enforcement is not active.
- Internet-assisted reasoning and candidate knowledge promotion are planned but not implemented.
- Real server-pushed per-step streaming is not implemented; the UI shows immediate client-side milestones and then the returned trace.
- Product naming remains unresolved because `Mnemosyne` collides with existing AI memory projects.

## Near-Term Priorities

1. Improve LLM planner recovery and transparency when tool calls fail.
2. Continue moving context strategy from deterministic direct retrieval toward the memory-agent/controller interface.
3. Build human-readable ingestion logs.
4. Add controlled repository refresh/rebuild with ingestion epochs.
5. Improve chronological source-date extraction and corpus ordering.
6. Expand context-document structure toward the target schema.
7. Add internet temporary context and candidate-knowledge boundaries.
8. Begin higher-quality semantic ingestion using local LLM calls.
9. Continue UI refinement through real use on standard and e-paper displays.

## Supporting Documents

- `docs/current-product-requirements-and-design.md`: recent product/UI requirements and implemented behavior.
- `docs/lifecycle-next-phase-requirements.md`: next-phase transparency, quality, repository refresh, internet, chronology, and multi-corpus requirements.
- `docs/requirements-design-addendum.md`: implementation decisions and inferred requirements accumulated during development.
- `docs/agentic-retrieval-process.md`: detailed current Python/LLM sequence for agentic retrieval and final answer generation.
- `docs/governance-schema-plan.md`: planned identity, governance, process, and trust schemas.
- `docs/mnemosyne-cognitive-architecture-draft.md`: forward-looking governed cognitive architecture concept.
- `docs/practical-applications.md`: possible application lanes and FOSS integration criteria.
