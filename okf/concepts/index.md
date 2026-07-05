---
type: Concept Index
title: Tirzah Concepts
description: The design ideas behind Tirzah — the provenance-aware graph memory, the three retrieval modes, hybrid + semantic ranking, the deep-retrieval agent, context compilation, sessions/continuity, and observational governance.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/docs/architecture-decisions.md
tags: [tirzah, concepts, architecture]
timestamp: 2026-06-19T00:00:00Z
---

# Concepts

Design decisions are recorded in
[`docs/architecture-decisions.md`](https://github.com/gellsmore-svg/tirzah/blob/main/docs/architecture-decisions.md);
the deep-retrieval design is [`docs/retrieval-agent-design.md`](https://github.com/gellsmore-svg/tirzah/blob/main/docs/retrieval-agent-design.md).

- **[Graph memory](graph-memory.md)** — sources become provenance-aware trees of
  nodes; rebuilds supersede rather than delete.
- **[Retrieval modes](retrieval-modes.md)** — `direct`, `agentic`, and `deep`.
- **[Hybrid & semantic retrieval](hybrid-and-semantic.md)** — lexical + vector
  blended ranking, and meaning-based `semantic_search`.
- **[Deep retrieval](deep-retrieval.md)** — the Python-orchestrated agent loop
  (ADR-020).
- **[Context compilation](context-compilation.md)** — role-tagged context rendered
  to a budgeted prompt, not raw documents.
- **[Sessions & continuity](sessions-and-continuity.md)** — exchanges, active
  documents, and restart state.
- **[Governance](governance.md)** — agent identities, process objects, and trust
  (observational in V1).
- **[Interpretive plan execution](interpretive-planning.md)** — Cairn plans
  walked live (SPEC §4.6): tool gating, constructs, mid-step revision, resume.
- **[Human-defined Processes](processes.md)** — selectable process templates that
  ground agentic work with configurable oversight (gates, deviations, audit).
