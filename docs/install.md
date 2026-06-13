# Install Tirzah

Tirzah V1.1 packaging is Docker-first for WSL/Linux. Docker Compose runs both
the web/CLI app and MongoDB with persistent volumes.

## Docker Quickstart

From a fresh clone:

```bash
docker compose build
docker compose run --rm app tirzah init --docker
docker compose up
```

Open `http://127.0.0.1:8765/`.

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
- `hoglah`: queue answer generation through the optional Hoglah package. This
  keeps Tirzah's retrieval layer unchanged while letting Hoglah manage local
  Ollama job execution.

You can skip prompts with:

```bash
tirzah init --non-interactive --runtime mock
tirzah init --non-interactive --runtime ollama_http
```

For Hoglah-backed answers, install the optional extra and initialize that
runtime profile:

```bash
pip install "tirzah[hoglah]"
tirzah init --non-interactive --runtime hoglah
```

In Docker mode, `tirzah init --docker --runtime hoglah` writes
`runtime.hoglah_ollama_host: http://host.docker.internal:11434` so Hoglah can
reach an Ollama daemon running on the host.

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
tirzah ingest-one tests/fixtures/v1-smoke-source-template.md --label install_smoke
tirzah list-docs --format text --limit 5
```

For the web UI, use the Developer toggle to inspect documents, active sessions,
profile status, graph inspection, and process runs.
