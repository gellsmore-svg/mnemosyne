---
type: Module Index
title: Tirzah Modules
description: The code packages — retrieval, ingestion, sessions, storage (db), adapters, config, and the web app.
resource: https://github.com/gellsmore-svg/tirzah/tree/main/src/tirzah
tags: [tirzah, modules, code]
timestamp: 2026-06-19T00:00:00Z
---

# Modules

- **[Retrieval](retrieval.md)** (`retrieval/`) — `queries.py` (search, hybrid,
  context), `deep.py` (the deep agent), `trust.py`.
- **[Ingestion](ingestion.md)** (`ingestion/`, `models/`) — parsing sources into
  the [graph](../concepts/graph-memory.md), the worker, and embedding/profile
  backfills.
- **[Sessions](sessions.md)** (`sessions/`) — `interaction.py` (the answer
  pipeline), continuity, active documents, exchanges, endorsements.
- **[Storage](storage.md)** (`db/`) — the Mongo store, repositories, queue,
  governance, indexes, schema.
- **[Adapters](adapters.md)** (`adapters/`) — answer / embedding / ingestion model
  adapters (mock, Ollama, Hoglah).
- **[Config](config.md)** (`config.py`) — `RuntimeConfig`, `RetrievalConfig`,
  `MongoConfig`.
- **[Web](web.md)** (`web/app.py`) — the Ask workspace + developer mode.
