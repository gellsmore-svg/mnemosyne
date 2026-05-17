# Source Documents

Last updated: 2026-05-17

## Imported Documents

| File | Role | Version | Status |
|---|---|---:|---|
| `LLM_Memory_Architecture_Requirements_v0.3.md` | Requirements document | 0.3 | working draft, for review |
| `Mnemosyne_Technical_Design_v0.1.md` | Technical design document | 0.1 | working draft, for review |

## Requirements Document Summary

The requirements document defines Mnemosyne as a five-layer memory system:

1. Ingestion Layer
2. Consolidation Layer
3. Retrieval Layer
4. Reasoning Layer
5. Session Continuity Layer

It specifies the core functional requirements for:

- document ingestion;
- ingestion failure handling;
- async consolidation / REM;
- semantic map;
- navigable graph traversal;
- context document compilation;
- output ingestion;
- provenance endorsement;
- active document registry;
- session continuity;
- context label types;
- local web interface;
- local-first non-functional constraints.

## Technical Design Summary

The technical design selects:

- Python 3.11+ as the core language;
- Hugging Face Transformers plus `llama-cpp-python` for local LLM runtime;
- model adapter abstraction for Gemma and future model backends;
- MongoDB 7.x as the primary storage layer;
- MongoDB-backed queue for the prototype;
- `sentence-transformers` for lightweight embedding pre-clustering;
- FastAPI plus vanilla HTML/CSS/JavaScript for the local interface;
- APScheduler for folder polling and REM scheduling;
- `config.yaml` as the single configuration source.

