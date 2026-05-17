# Open Questions

Last updated: 2026-05-17

These are questions that affect implementation direction. Answered items are kept here until their implementation is visible in code.

## Answered Decisions

- Stage 1 uses real local MongoDB immediately. A fake repository may still be used for narrow unit tests, but the first working path targets MongoDB.
- MongoDB 8.0.23 has been installed on WSL Ubuntu 24.04 and verified with `mongosh --eval 'db.runCommand({ ping: 1 })'`.
- Stage 1 hardware target is the current machine: 8GB RAM plus GTX 3060. Avoid assuming the later 32GB RAM target.
- The first user surface is a CLI/dev command for ingestion and inspection. The FastAPI web UI follows after the ingestion slice is stable.
- Implicit endorsement is represented as a label/provenance field in the correct MongoDB tree/node structure, not as a filename convention.
- Duplicate source ingestion is rejected by content checksum and the requestor is notified.
- Accepted source files are copied into the local archive.
- Label meanings are stored in MongoDB in a lookup collection with descriptions.
- `llama-cpp-python` is only an optional future adapter path for direct local GGUF model execution; it is not a required Stage 1 dependency.

## Product Scope

1. Is Mnemosyne primarily for interactive personal use, or should it become a reusable service/library for other agents and applications?
2. Should the first version be single-user only?
3. Should project/session clusters map directly to domain folders like `/home/cello/domains/*`, or should they be independent concepts?

## Local Runtime

4. Answered: Stage 1 assumes current 8GB RAM plus GTX 3060.
5. Partly answered: do not force `llama-cpp-python` into Stage 1. Keep a model adapter boundary and use deterministic/mock output for early tests.
6. Which Gemma model files are already available locally, and where should `config.yaml` point?

## MongoDB And Vector Search

7. Answered: MongoDB 8.0.23 is installed locally and `mongod` is active under systemd.
8. Answered: use real MongoDB immediately for the working Stage 1 path.
9. Does the target local MongoDB support the intended vector search path, or should Stage 3 plan for a fallback vector index?

## Ingestion Semantics

10. Should Stage 1 allow a deterministic chunker fallback for tests and cold-start, or should all chunking pass through Gemma from the beginning?
11. Answered: implicit endorsement is a MongoDB tree/node label/provenance value, not a filename convention.
12. Answered: copy accepted source documents into the local archive.

## Interface

13. Answered: build a CLI/dev command first for faster ingestion testing.
14. Should the interface support explicit "endorse this" actions in addition to natural language endorsement detection?

## Evaluation

15. What should be the first evaluation corpus?
16. What is the brute-force baseline model/context window for comparison?
17. What metrics matter most for the first evaluation: token bloat ratio, answer quality, recall coverage, or continuity preservation?
