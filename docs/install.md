# Install Tirzah

Tirzah V1.2 packaging is Docker-first for WSL/Linux. Docker Compose runs both
the web/CLI app and MongoDB with persistent volumes. V1.2 also includes optional
Hoglah-backed answer and embedding adapters.

## Docker Quickstart

From a fresh clone:

```bash
docker compose build
docker compose run --rm app tirzah init --docker
docker compose up
```

Open `http://127.0.0.1:8765/`.

Compose maps the app to **host loopback only** (`127.0.0.1:8765`). Inside the
container the process may listen on `0.0.0.0`, but:

- `runtime.web_localhost_only` defaults to **true** — non-loopback API clients
  receive `403 localhost_required`.
- Set `runtime.web_api_token` (or env `TIRZAH_WEB_API_TOKEN`) to require
  `X-Tirzah-Api-Token` / `Authorization: Bearer …` on every `/api/*` route
  except `/api/health`. Unknown config keys are rejected (typos no longer
  silently disable auth).

The init command creates `config.yaml` and the local data directories. In Docker
mode it writes `mongo.uri: mongodb://mongo:27017`, so the app talks to the
Compose-managed MongoDB service.

For a fully non-interactive first run:

```bash
docker compose run --rm app tirzah init --docker --non-interactive
docker compose up -d
docker compose exec app tirzah db-ping
```

Stop the app without deleting data:

```bash
docker compose down
```

Delete all Docker-managed Tirzah and Mongo data:

```bash
docker compose down -v
```

## Runtime Choices

`tirzah init` is interactive by default. It can write one of these runtime
profiles:

- `mock`: deterministic local answer/profile adapters for smoke tests and first
  use.
- `ollama_cli`: call a local Ollama executable from a Python install.
- `ollama_http`: call an existing Ollama HTTP server. In Docker mode this uses
  `http://host.docker.internal:11434`.
- `local_command`: mock answers plus the packaged `tirzah-profile-helper`
  command for local text-similarity profiles.
- `hoglah`: queue answer generation through the optional Hoglah package. Tirzah
  is a pure submitter into a local SQLite queue; a separate Hoglah daemon
  executes jobs against Ollama and writes results back for Tirzah to consume.

You can skip prompts with:

```bash
tirzah init --non-interactive --runtime mock
tirzah init --non-interactive --runtime ollama_http
```

## Milcah Specialist Calls

Recursive planning can optionally dispatch coherence pressure-tests and
counter-framework research to [Milcah](https://github.com/gellsmore-svg/Milcah):

```bash
pip install "tirzah[milcah]"
```

Then set `runtime.milcah_enabled: true` in `config.yaml`, or export
`MILCAH_ENABLED=1`. `runtime.milcah_model` / `MILCAH_MODEL` can pin the model
Milcah should use. If Milcah is absent or the call fails, Tirzah records a
blocked specialist result and continues without treating that as graph memory.
Milcah's default specialist runner submits role calls through Hoglah, so start a
Hoglah worker as below when you want live model-backed specialist results.

## Hoglah Answer Queue

For Hoglah-backed answers, install the optional extra and initialize that
runtime profile:

```bash
pip install "tirzah[hoglah]"
tirzah init --non-interactive --runtime hoglah
```

Then run a separate Hoglah worker against the same queue and output directory:

```bash
HOGLAH_OUTPUT_DIR=data/hoglah/outbox \
  hoglah run --real --db data/hoglah/jobs.sqlite3 \
  --ollama-host http://localhost:11434 -c 1
```

Set `runtime.answer_adapter: hoglah` and/or `runtime.embedding_adapter: hoglah`.
Tirzah submits jobs and waits for terminal results by polling
`runtime.hoglah_output_dir` (`runtime.hoglah_delivery: poll`) or by running a
tiny local callback receiver (`runtime.hoglah_delivery: callback`, with file
poll fallback). The `hoglah` embedding adapter is allowed for memory operations
without `allow_http_ingestion_adapters` because Tirzah itself only performs
local IPC; the Hoglah daemon owns the Ollama HTTP call.

For callback delivery, `runtime.hoglah_callback_port: 0` lets Tirzah choose an
ephemeral local port and pass that callback URL with each job. Use polling when
the Hoglah daemon cannot reach Tirzah's callback host and port, such as across
some container or WSL network boundaries.

In Docker mode, the Hoglah daemon must be able to see the same queue/output
storage as Tirzah. If the daemon runs on the host while Tirzah runs in Compose,
mount or share `data/hoglah/` consistently and use the host-visible Ollama URL,
for example `http://host.docker.internal:11434`.

## Python Developer Install

Use this path when you want to run directly on the host and manage MongoDB
yourself:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev,profiles]"
.venv/bin/tirzah init --non-interactive
.venv/bin/tirzah db-ping
.venv/bin/tirzah serve
```

The host install expects MongoDB at `mongodb://localhost:27017` unless you edit
`config.yaml`.

## Smoke Check

After install:

```bash
tirzah db-ping
tirzah memory-health
tirzah ingest-one tests/fixtures/v1-smoke-source-template.md --label install_smoke
tirzah list-docs --format text --limit 5
```

For the web UI, use the Developer toggle to inspect documents, active sessions,
profile status, graph inspection, and process runs.

## Native Linux reference configuration

A verified **8 GB RAM / CPU-only Ollama** example (Debian 13, `gemma3:1b`,
MongoDB, functional smokes + pytest) is documented in
[`native-linux-compatibility-report.md`](native-linux-compatibility-report.md).
