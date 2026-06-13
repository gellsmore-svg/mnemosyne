# Architecture Decisions

Last updated: 2026-05-17

## Accepted Decisions

| ID | Decision | Rationale |
|---|---|---|
| ADR-001 | Use Python 3.11+ for core orchestration. | Best fit for local LLM tooling, MongoDB, FastAPI, and worker scripts. |
| ADR-002 | Use MongoDB as primary storage. | Flexible document shape supports adjacency-list graph, queues, sessions, semantic map, and evolving schema. Local WSL install is MongoDB 8.0.23. |
| ADR-003 | Avoid a dedicated graph database for the prototype. | MongoDB adjacency list is enough for the first implementation and simpler to operate locally. |
| ADR-004 | Put all LLM calls behind a model adapter. | Allows Gemma to be replaced or supplemented without rewriting ingestion/retrieval code. |
| ADR-005 | Use a MongoDB-backed queue initially. | Sufficient for prototype reliability while avoiding Kafka/RabbitMQ overhead. |
| ADR-006 | Use FastAPI plus vanilla frontend. | Minimal local interface with streaming and tabs; no frontend build complexity. |
| ADR-007 | Use APScheduler for polling and REM schedule. | Lightweight in-process scheduling is enough for the first build. |
| ADR-008 | Use sentence-transformers for embedding pre-clustering. | Cheap CPU-friendly candidate selection before Gemma confirmation. |
| ADR-009 | Treat `.restart.md` as rendered state, not source of truth. | Source of truth is the restart state node in MongoDB once implemented. |
| ADR-010 | Track provenance at chunk level. | Matches endorsement semantics and avoids over-trusting whole documents by default. |
| ADR-011 | Use real MongoDB in Stage 1. | The project should exercise the actual persistence shape from the beginning; mocks are limited to unit-test seams. |
| ADR-012 | Build CLI/dev commands before the web UI. | Ingestion, inspection, and failure recovery need a fast developer loop before user-facing session workflows. |
| ADR-013 | Target current 8GB RAM plus GTX 3060 for Stage 1. | Early implementation must avoid assumptions that only hold after a RAM upgrade. |
| ADR-014 | Store endorsement as MongoDB tree/node metadata. | Endorsement is a semantic/provenance label in the graph, not a filename convention. |
| ADR-015 | Keep `llama-cpp-python` optional. | Direct GGUF execution may be useful later, but Stage 1 should rely on an adapter boundary and deterministic/mock behavior while storage and ingestion settle. |
| ADR-016 | Reject duplicate files by SHA-256 checksum. | Duplicate detection should be content-based, notify the requestor, and avoid relying on filenames or paths. |
| ADR-017 | Copy accepted sources into the archive. | The prototype needs durable local provenance even if the original file moves or changes. |
| ADR-018 | Store label definitions in MongoDB. | Labels need machine-readable keys and human-readable descriptions close to the graph data that uses them. |
| ADR-019 | Hoglah adapters use the decoupled queue-daemon topology for both answers and embeddings. | Brings the `hoglah` answer adapter to parity with Mahalath and adds a `hoglah` embedding adapter. Tirzah is a PURE SUBMITTER (Hoglah client `start_worker=False`); a SEPARATE `hoglah run --real` daemon services the shared SQLite queue and delivers terminal results by output-folder poll or HTTP callback (Tirzah supplies its own callback URL per job — Hoglah hardcodes nothing). Replaces the earlier in-process `Hoglah(use_real=True)` + `wait()` answer path. Embeddings route through Hoglah embedding jobs (Hoglah ADR-013); requires hoglah>=0.3.0. The `hoglah` embedding adapter is intentionally NOT in `HTTP_BACKED_EMBEDDING_ADAPTERS`: from Tirzah's process it is local IPC (local SQLite queue + local result file / localhost callback), so it is permitted for memory ops without `allow_http_ingestion_adapters` — operator-sanctioned 2026-06-13, accepting that the daemon then calls Ollama over HTTP. Shared submit/await + callback-receiver machinery lives in `adapters/hoglah_runtime.py`. |

## Open Or Pending Decisions

| ID | Question | Current Lean |
|---|---|---|
| DQ-001 | Exact Stage 1 implementation boundary. | Ingest markdown/plaintext first; defer code/web extraction unless easy. |
| DQ-002 | Exact structured context document schema. | Use hybrid JSON with summaries, chunks, provenance, relationships, and sufficiency flags as in design Section 7.8. |
| DQ-003 | Traversal scoring signal reliability. | Track mechanically from compiled context document and exchanges; validate in Stage 3. |
| DQ-004 | Low-confidence edge threshold. | Default 5.0, configurable. |
| DQ-005 | Contradictions as first-class edges. | Add `contradicts` relation type early, even if advanced conflict handling is later. |
| DQ-006 | Model role allocation. | Current hardware is 8GB RAM plus GTX 3060; prefer lightweight/local role choices until upgraded. |
| DQ-007 | Whether MongoDB local supports vector search in target environment. | Confirm before depending on `$vectorSearch`; fallback may be local FAISS/Chroma or brute embedding scan for prototype. |
