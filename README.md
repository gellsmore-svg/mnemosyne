# Tirzah

**Tirzah** is a locally operated, graph-based memory and retrieval layer for LLM
interactions. Instead of brute-force-loading whole documents into a prompt, it
ingests sources into a provenance-aware graph in MongoDB and compiles
structured, navigable, source-faithful context for a local model to answer over.

It runs entirely on local infrastructure — MongoDB for storage and Ollama for
inference — and is usable from a CLI or a web interface.

## Features

- **Graph memory** — documents are ingested into hierarchical trees
  (`source_root` → `source_section` → `source_chunk`) of nodes, with the source
  text preserved verbatim.
- **Provenance-aware** — every node carries its source path, checksum, labels,
  and endorsement/provenance fields; rebuilds create a new ingestion epoch and
  mark prior trees as `superseded` rather than deleting them.
- **Structured retrieval** — search nodes by text/label, then compile
  role-tagged context (focus, ancestors, siblings, descendants) and render it to
  a budgeted prompt — rather than dumping raw documents.
- **Agentic mode** — an iterative memory-agent loop lets a first model call pick
  read-only retrieval tools (`search_nodes`, `compile_context`, `list_documents`)
  before the answer call.
- **Local-first** — MongoDB + Ollama; no cloud dependency. A deterministic mock
  adapter keeps tests and first runs reproducible and offline.
- **Session continuity** — each exchange records a database-backed restart-state
  snapshot you can inspect or render on demand (see [Restart state](#restart-state)).
- **CLI and web UI** — a work-first Ask workspace for normal use, with a
  developer mode that exposes retrieval traces, ingestion, and queue controls.
- **Optional queue routing** — route model calls through
  [Hoglah](https://github.com/gellsmore-svg/hoglah) so every inference is
  serialized through one durable, restart-safe queue.

> **Status:** early V1 — the "local memory workbench" release. Several pieces are
> scaffold-depth (lexical retrieval, observational governance, mock-default
> embeddings). See [`docs/build-roadmap.md`](docs/build-roadmap.md) and
> [`docs/v1-readiness-checklist.md`](docs/v1-readiness-checklist.md).

## Install

The supported path is Docker Compose on WSL/Linux:

```bash
docker compose build
docker compose run --rm app tirzah init --docker
docker compose up
```

Then open <http://127.0.0.1:8765/>. For Python/developer installs and runtime
configuration, see [`docs/install.md`](docs/install.md).

The CLI is `tirzah` (the legacy `mnemosyne` command remains as a compatibility
alias during the rename transition).

## Quick start

```bash
# Verify the database connection
tirzah db-ping

# Ingest a folder of documents into the graph
tirzah ingest-folder /path/to/docs --label my_corpus

# Inspect what was ingested
tirzah list-docs --limit 5
tirzah search-nodes --query "topic of interest" --label source_chunk

# Ask a question (uses the local Ollama model by default)
tirzah ask "What does the corpus say about X?" --retrieval-mode agentic
tirzah ask "Research the current evidence for X" --web

# Or start the web UI
tirzah serve
```

`ask`/`chat` use the retrieval pipeline and the local Ollama CLI answer adapter
by default (`gemma3:1b` per `config.example.yaml`); pass `--model <name>` to
override per request, or `--adapter mock` for an offline deterministic answer.

## Recursive Cairn request planning

The browser-facing `/api/ask` path is wrapped by a recursive process planner. It
creates a bounded first-pass Cairn `PLAN`, invokes Tirzah's existing validated
retrieval and answer pipeline, and then revises the same plan from new evidence
or unresolved state. Each revision keeps the plan ID, parent revision, trigger,
stopping conditions, and a complete process backbone.

Plans are operational records in `recursive_plans`, not trusted graph memory, and
they do not grant tools or side-effect authority. Python continues to enforce the
actual tool menu, budgets, writes, and termination. Later information can revise
a stored plan through `POST /api/plans/{plan_id}/revise`. Configure the planner
with `runtime.recursive_planning_*`.

When the planner needs a coherence pressure-test or counter-framework research,
it can call [Milcah](https://github.com/gellsmore-svg/Milcah) as an optional
specialist:

```bash
pip install "tirzah[milcah]"
```

Set `runtime.milcah_enabled: true` (or `MILCAH_ENABLED=1`). Tirzah delegates to
Milcah's `coherence_check` contract and degrades to a blocked specialist result
if Milcah is not importable or the call fails.

## Web UI

```bash
tirzah serve            # http://127.0.0.1:8765/
```

The default **Ask** workspace is work-first: create/select a session, enter a
prompt, choose an Ollama model, press Ask, and read the response and a plain
activity log — a normal local LLM client.

Toggle **Developer** mode (or `?developer=1`) to reveal Browse/Ingestion tabs,
focus-node and adapter overrides, retrieval-mode selection, raw prompt/trace
output, queue/status controls, and semantic-edge review.

## Restart state

Resumable session state is tracked in the database, not a file: each exchange
records a `session_continuity` snapshot — the latest query, focus/used nodes,
active documents, controller decision, evidence summary, and an answer preview,
with older iterations superseded but retained.

```bash
tirzah session-continuity --session-id default --limit 5   # latest + recent
tirzah restart-render --session-id default --output .restart.md  # rendered view
```

It is also exposed over HTTP at `GET /api/session-continuity` (with a panel in
the web UI's developer mode).

## Routing model calls through Hoglah (optional)

Route **answers and embeddings** through [Hoglah](https://github.com/gellsmore-svg/hoglah),
a local-first job queue, so every model call is serialized through one durable
queue that survives restarts:

```bash
pip install "tirzah[hoglah]"
```

Set `runtime.answer_adapter` and/or `runtime.embedding_adapter` to `hoglah`, then
run a **separate** worker daemon pointed at the same queue and output folder:

```bash
HOGLAH_OUTPUT_DIR=data/hoglah/outbox \
  hoglah run --real --db data/hoglah/jobs.sqlite3 \
  --ollama-host http://<host>:11434 -c 1
```

Tirzah becomes a pure submitter (no in-process worker): it enqueues each call and
collects the result by polling the output folder (`hoglah_delivery: poll`) or via
an HTTP callback (`hoglah_delivery: callback`, with poll as fallback). Tirzah
itself does only local IPC; the daemon makes the Ollama call.

## Similarity profiles / embeddings (optional)

Text-similarity profiles default to the deterministic `mock` adapter so tests and
first runs are reproducible. For real local embeddings, install the optional
extra and configure a local embedding adapter:

```bash
pip install -e '.[profiles]'
```

`tirzah init --runtime local_command` writes sensible defaults (e.g.
`BAAI/bge-small-en-v1.5`, 384 dims, worker mode). Existing nodes can be profiled
in bounded, resumable batches (`backfill-profiles`, `queue-profile-backfill`,
`process-profile-backfill`). HTTP-backed embedding adapters are blocked by default
for ingestion/retrieval. See [`docs/install.md`](docs/install.md) for details.

## Documentation

The project's design and requirements live under [`docs/`](docs/). Good entry
points:

- [`docs/project-brief.md`](docs/project-brief.md) — what Tirzah is and why
- [`docs/consolidated-requirements-and-design.md`](docs/consolidated-requirements-and-design.md) — current requirements + design
- [`docs/architecture-decisions.md`](docs/architecture-decisions.md) — ADRs
- [`docs/build-roadmap.md`](docs/build-roadmap.md) — staged plan and status
- [`docs/v1-known-limitations.md`](docs/v1-known-limitations.md) — known gaps

The original requirements/design documents (`LLM_Memory_Architecture_Requirements_v0.3.md`,
`Mnemosyne_Technical_Design_v0.1.md`) are kept at the repo root for provenance.

## Reporting issues

Bugs and questions: <https://github.com/gellsmore-svg/tirzah/issues>. Security
issues: please report privately — see [SECURITY.md](SECURITY.md). Tirzah is an
early local-first prototype and is not hardened for untrusted network exposure.

## Knowledge bundle

A machine- and human-readable knowledge map of Tirzah's concepts, modules, and CLI
is published as an [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
bundle under [`okf/`](okf/index.md) — markdown with YAML frontmatter, linked into a
concept graph.

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
