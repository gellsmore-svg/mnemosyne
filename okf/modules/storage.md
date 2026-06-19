---
type: Module
title: Storage (db)
description: The MongoDB layer behind a memory-store seam — node/document repositories, the ingestion inbox queue, governance collections, index management, schema, and serializers; an in-memory store backs offline tests.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/db/memory_store.py
tags: [tirzah, storage, mongodb, db]
timestamp: 2026-06-19T00:00:00Z
---

# Storage (`db/`)

The persistence layer for the [graph memory](../concepts/graph-memory.md):

- **`client.py`** — the MongoDB connection (`MongoConfig`).
- **`memory_store.py`** — the store seam (`MemoryStore`): node/document reads and
  writes used across retrieval and ingestion. An in-memory implementation backs
  fast offline tests; a `real_mongo` marker covers real-Mongo tests.
- **`repositories.py`** — higher-level repository operations over nodes, documents,
  edges, and semantic candidates.
- **`queue.py`** — the ingestion inbox queue (enqueue / process).
- **`governance.py`** — agent identities, process objects, policies, runs, and
  trust profiles (see [governance](../concepts/governance.md)).
- **`indexes.py`, `schema.py`, `serializers.py`, `health.py`** — index management,
  the node/document schema, (de)serialization, and `memory-health` diagnostics.

Mongo is authoritative; the database is `mnemosyne_dev` by default. Storage is
consumed by [retrieval](retrieval.md), [ingestion](ingestion.md), and
[sessions](sessions.md).
