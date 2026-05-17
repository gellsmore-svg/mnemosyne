# Mnemosyne Project Brief

Last updated: 2026-05-17

## Purpose

Mnemosyne is a locally operated, graph-based memory and retrieval layer for LLM work. It sits between the user and a reasoning LLM, assembling structured, provenance-aware context instead of dumping whole documents into the context window or delegating memory decisions to an opaque agent.

The system is designed to improve as the corpus grows, especially once the available corpus exceeds the effective context window of the reasoning model.

## Core Goals

- Reduce token waste versus brute-force document loading.
- Preserve thought, process, and session continuity across context windows and sessions.
- Maintain human-visible provenance and trust tiers at chunk level.
- Let Gemma navigate a graph of chunks, trees, documents, sessions, and semantic relationships.
- Operate locally by default, with optional external LLM calls only when explicitly requested.
- Provide a measurable comparison against brute-force retrieval.

## Non-Goals For The First Build

- Cloud-first operation.
- A dedicated graph database.
- A complex frontend framework.
- Full production queue infrastructure such as Kafka or RabbitMQ.
- Fully automatic trust elevation of generated content.
- Replacing human review for endorsement decisions when target chunks are ambiguous.

## Primary Users

Initial user: the local operator/developer using Mnemosyne to maintain long-running software, research, and reasoning projects.

Likely future users:

- developers working across long code/research threads;
- researchers managing evolving document corpora;
- writers maintaining persistent project memory;
- local-first AI users who want inspectable memory and provenance.

## First Useful Outcome

The first useful system should ingest one text document transactionally, create chunk nodes and tree records in MongoDB, and make those chunks inspectable enough to verify that the schema, provenance, context labels, and failure handling work on real source documents.

This corresponds to Stage 1 in the requirements document.

