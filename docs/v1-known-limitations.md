# V1 Known Limitations

Date: 2026-06-14

Tirzah V1 is complete as a local memory workbench: ingestion, inspection, retrieval, sessions, active documents, generated-output review, semantic-edge review, governance listings, and readable activity logs are implemented across CLI and web surfaces. The limits below describe what remains scaffolded or post-V1, so release and checklist language stays precise.

## Persistence And Recovery

- Ingestion and rebuild writes use best-effort rollback/restore behavior rather than MongoDB transactions or a two-phase commit.
- Embeddings are generated during node insertion for normal ingestion paths. Larger deployments should prefer explicit profile backfill and stronger interruption recovery.
- Maintenance paths are being batched incrementally, but not all rebuild/backfill operations are optimized for large corpora.

## Retrieval Quality

- Primary retrieval is still lexical and locally reranked; vector/profile signals exist but are not yet a full hybrid lexical+vector retrieval path.
- Trust and temporal diagnostics are exposed for inspection, but they do not yet affect default ranking.
- Relevance gating remains V1-scaffold depth for broad prompts; stronger thresholds and per-node "why included" explanations are post-V1 work.

## Continuity

- Sessions, exchanges, active documents, used nodes, and context metadata are persisted.
- The richer last-prompt-iteration artifact described in the requirements is not yet first-class: rejected chunks, full controller proposal, final context package, unresolved follow-ups, and a dedicated continuity panel remain post-V1 work.

## Governance

- Agent identities, process objects, policies, process runs, and trust profiles are seedable and inspectable.
- Governance is observational in V1. Process steps, approvals, and behavioral expectations are not automatically enforced.

## Ingestion Intelligence

- The V1 ingestion baseline is deterministic heading/paragraph parsing through the mock ingestion adapter.
- Review-gated LLM-assisted chunking, relation extraction, and richer metadata generation remain post-V1 work.

## Operations And Naming

- The preferred package and CLI name is `tirzah`; the `mnemosyne` command and some historical defaults remain for compatibility during the rename transition.
- The CLI and web API expose broad operator surfaces, but some modules are intentionally monolithic at V1 and need post-V1 refactoring.
- Most automated tests are fast unit tests with fakes. Real-Mongo smoke coverage exists, but a broader real-Mongo integration profile is still desirable.
