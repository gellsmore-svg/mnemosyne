# Practical Applications And Integration Targets

Date: 2026-05-22

Status: planning note. This document captures likely application surfaces for Mnemosyne so core memory work keeps practical integration constraints in view. It does not mark these integrations as implemented.

## Purpose

Mnemosyne is being built as a local memory engine, not as a single all-purpose assistant. The practical goal is to expose durable memory, retrieval, provenance, endorsement, and context compilation to tools that already handle user interaction, coding workflows, web access, or voice input.

The core design should therefore prefer stable interfaces over one tightly coupled application shell.

## Application Lanes

### Web-To-Local Corpus

Goal: strengthen the local corpus with useful online material while preserving source provenance and review boundaries.

Expected workflow:

1. A user or external tool supplies a URL, feed item, PDF, page snapshot, repository file, issue thread, or other online source.
2. Mnemosyne downloads or receives the content as a local source file.
3. The source is stored under runtime data with attribution, retrieval date, original URL, content type, checksum, and licence notes when available.
4. The normal ingestion queue processes it rather than using a separate web-only memory path.
5. Generated nodes keep provenance back to both the local archived source and the original online location.
6. Review/endorsement decides whether imported material becomes trusted memory, reference-only material, or rejected/noisy material.

Design pressure:

- Web data is mutable, noisy, and often licence-sensitive.
- Imported source text must not be silently rewritten or summarised before archival.
- Optional web search should produce queued ingestion candidates, not unreviewed facts injected directly into long-term memory.
- The memory-agent should eventually be able to say when the local corpus is insufficient and propose web acquisition as a separate action.

Near-term useful interface:

- `import-url` or `enqueue-url` command that fetches a source into `data/online_sources/` and queues normal ingestion.
- Source metadata fields for original URL, retrieval timestamp, content type, licence/terms note, and fetch adapter.
- A review surface for accepting, labelling, or rejecting imported online documents.

### Coding Support And CLI Agency

Goal: let coding agents and shell workflows use Mnemosyne as a project memory backend.

Expected workflow:

1. A coding tool asks Mnemosyne for project context: decisions, recent failures, architecture notes, known commands, active files, previous reviews, and open risks.
2. Mnemosyne retrieves evidence from local graph memory and returns a bounded context document with provenance.
3. The coding tool executes work in its own environment.
4. Important outcomes are written back as exchanges, review notes, command results, generated outputs, or explicit memory capture requests.
5. Mnemosyne ingests and reviews those outputs before they become trusted retrieval material.

Design pressure:

- Mnemosyne should expose memory tools that Codex, Claude, local scripts, or other agents can call without sharing one UI.
- Memory capture should distinguish command output, code review, user instruction, design decision, bug finding, and generated implementation notes.
- CLI agency should be permission-aware. Mnemosyne can remember and suggest actions, but command execution remains controlled by the host agent/tool.
- Tool outputs should be resumable and inspectable, with exchange IDs and source links.

Near-term useful interface:

- Read-only CLI/API tools for exact document, tree, node, exchange, and active-document lookup.
- A compact `context-for-task` command that compiles project memory for a coding prompt.
- A `capture-note` or `capture-event` command for explicit user/project memories.
- Structured event types for `command_result`, `code_review`, `design_decision`, `bug_fix`, and `open_question`.

### Voice Prompting Input

Goal: support quick spoken capture and retrieval without making speech recognition part of the memory core.

Expected workflow:

1. A voice tool records audio and produces a transcript.
2. Mnemosyne receives the transcript plus metadata: timestamp, source device/app, speaker if known, confidence, and correction status.
3. The transcript is stored as an exchange or source document depending on intent.
4. User corrections or explicit endorsement update the memory record.
5. Retrieval treats corrected transcript text as primary and raw transcript/audio metadata as provenance.

Design pressure:

- Voice input is often ambiguous, partial, and correction-heavy.
- The memory engine should preserve enough metadata to distinguish raw transcript from user-confirmed memory.
- Voice capture is best treated as an input adapter, not a separate memory model.
- "Remember this" and "what was I saying about..." should map cleanly to existing session, active-document, and endorsement mechanisms.

Near-term useful interface:

- A transcript ingestion endpoint accepting text plus confidence/correction metadata.
- A capture mode for short continuity-critical notes.
- A correction flow that can replace or annotate the transcript while preserving the raw provenance.

### FOSS Tool Integration

Goal: evaluate whether existing open-source tools can supply UI, agent orchestration, web fetching, or voice input while Mnemosyne remains the memory backend.

Selection criteria:

- The tool can call an external local HTTP API, CLI command, MCP server, or plugin.
- The tool can accept retrieved context without forcing its own vector store as the source of truth.
- It can preserve provenance and structured metadata, or at least pass them through.
- It works locally or can be configured for local-first use.
- It does not require Mnemosyne to abandon MongoDB graph storage, endorsement labels, or restart-state design.

Likely integration shapes:

- HTTP API: simplest for web UI, local apps, and service-style tools.
- CLI: best for shell and coding-agent workflows.
- MCP server: useful if Mnemosyne should expose tools directly to agent clients.
- File/drop-folder ingestion: useful for loose coupling with web fetchers, voice tools, and document managers.

Evaluation rule:

- Prefer using external tools for interaction and capture surfaces.
- Keep memory authority in Mnemosyne: ingestion, source preservation, graph records, retrieval scoring, context assembly, endorsement, and restart state.

## Interface Implications For Core Work

The application lanes above imply several core requirements:

- Stable read APIs for documents, nodes, sessions, exchanges, active documents, and compiled context.
- Stable write APIs for source ingestion, explicit capture, generated output ingestion, and review/endorsement.
- Machine-readable context envelopes, not only Markdown prompt text.
- Clear provenance fields for every imported, generated, or captured memory.
- Idempotent queueing and checksums for web/voice/tool imports.
- Permission boundaries that separate memory retrieval from external command execution or web browsing.
- Integration tests that exercise Mnemosyne as a service, not only as an internal Python package.

## Deferred Research

Before choosing integration tooling, run a focused FOSS scan for:

- local-first agent shells that can call external memory tools;
- MCP-compatible local assistants;
- web clipping/fetching tools with metadata export;
- speech-to-text capture tools that can POST transcripts or write files;
- coding assistants or CLI agents that can use external context APIs.

The scan should compare integration fit, not general assistant quality. A tool is useful if Mnemosyne can plug in as the durable memory backend without losing provenance, review, and graph retrieval semantics.
