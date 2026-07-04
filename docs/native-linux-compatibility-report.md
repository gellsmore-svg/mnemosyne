# Native Linux compatibility report (reference configuration)

**Status:** verified on a single host — use as an example of a compatible
8 GB-class setup, not a guaranteed minimum for every workload.

- **Report date:** 2026-07-04 (UTC)
- **OS:** Debian 13 (trixie), `linux 6.12.86+deb13-amd64`, x86_64
- **Host RAM:** 7.7 GiB total (≈8 GB class), CPU-only inference (no GPU)
- **Family versions (editable monorepo venv):** tirzah 1.3.0, hoglah 0.8.0,
  mahalath 1.1.0, keturah/galeed/cairn 0.1.0, milcah 0.2.0

## Verdict

| Concern | Result |
|---------|--------|
| Install spine packages in shared venv | ✅ |
| MongoDB-backed Tirzah + Mahalath | ✅ |
| Real Ollama inference (`gemma3:1b`) | ✅ |
| Peak RAM during LLM smoke | **4907 MB** — fits 8 GB with headroom |
| `docker compose build` (standalone tirzah clone) | ✅ after Dockerfile installs spine from GitHub |

Functional behaviour was confirmed (not just `pip install` / `npm ci`).

## Reference services

```bash
# MongoDB 8.0
docker run -d --name mongo-test -p 27017:27017 mongo:8.0

# Ollama 0.31.1 (native install)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:1b    # ~815 MB weights
```

| Service | Endpoint | Notes |
|---------|----------|-------|
| MongoDB | `mongodb://localhost:27017` | Required for tirzah + mahalath integration tests |
| Ollama | `http://localhost:11434` | `gemma3:1b` verified on CPU; no WSL gateway needed on native Linux |

## Python monorepo install (shared venv)

```bash
cd /path/to/github   # sibling clones: keturah, galeed, Cairn, hoglah, Milcah, mahalath, tirzah
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip wheel setuptools

pip install -e ./keturah/.[dev]
pip install -e ./galeed/.[dev]
pip install -e ./Cairn/.[dev]
pip install -e ./hoglah/.[dev,cli]
pip install -e ./Milcah/.[dev]
pip install -e ./mahalath/.[dev,web]
pip install -e ./tirzah/.[dev,web,profiles]
```

**System packages used:** `python3-venv`, `python3-pip`, `python3-dev`,
`build-essential`, `docker.io`, Node.js 20 + npm (for Mahlah/Mizpah/Relational-Substrate).

## Functional smoke (real LLM)

Run with MongoDB and Ollama up, `gemma3:1b` pulled.

```bash
source .venv/bin/activate

# Hoglah → Ollama
hoglah doctor --real
hoglah submit "Say hello in one word." --model gemma3:1b --real --wait

# Tirzah → MongoDB, then Ollama HTTP runtime
cd tirzah
tirzah init --non-interactive --runtime mock          # first-time only
tirzah db-ping
tirzah memory-health
tirzah ingest-one tests/fixtures/v1-smoke-source-template.md --label install_smoke
tirzah list-docs --format text --limit 5
tirzah init --non-interactive --runtime ollama_http   # switches adapters to Ollama
tirzah ask "What is 2+2? Reply with just the number." --model gemma3:1b
```

**Observed on reference host:**

| Step | Outcome |
|------|---------|
| `ollama run gemma3:1b` | OK |
| `hoglah doctor --real` | OK |
| `hoglah submit --real --wait` | OK (e.g. "Greetings!") |
| `tirzah db-ping` / `ingest-one` / `list-docs` | OK |
| `tirzah ask` (ollama_http) | OK |

**RAM:** peak **4907 MB** during the above (model ~815 MB + app + Ollama runtime).

## pytest summary (MongoDB running)

| Package | Result |
|---------|--------|
| keturah | 7 passed |
| galeed | 13 passed |
| Cairn | 5 passed |
| Milcah | 113 passed |
| tirzah | 674 passed |
| hoglah | 97 passed, 18 skipped |
| mahalath | 462 passed; 8 failed in `test_bundle.py` on full-suite run (Mongo duplicate-key isolation — re-run single test: pass) |

## Node apps (build only on reference host)

| Repo | `npm ci` + `npm run build` |
|------|----------------------------|
| Relational-Substrate | ✅ |
| Mahlah | ✅ |
| Mizpah | ✅ |

Mahlah/Mizpah **dev UI** against a live `tirzah serve` on `:8765` was not smoke-tested here.

## Docker (standalone tirzah clone)

```bash
cd tirzah
DOCKER_BUILDKIT=0 docker-compose build   # or: docker compose build
```

Spine packages (`keturah`, `galeed`, `cairn`) are installed from GitHub during image
build (not PyPI). Build succeeded on the reference host after that Dockerfile change.

## Example runtime config (Ollama HTTP)

After `tirzah init --non-interactive --runtime ollama_http`, the essentials are:

```yaml
mongo:
  uri: mongodb://localhost:27017
runtime:
  answer_adapter: ollama_http   # set by init
  ollama_base_url: http://localhost:11434
  ollama_model: gemma3:1b
```

See `config.example.yaml` for the full schema.

## Not verified on this host

- `tirzah serve` + Mahlah/Mizpah browser smoke
- `RUN_OLLAMA_TESTS=1` hoglah integration pytest flag
- Kafka / RabbitMQ / Redis messaging bridges
- Models larger than `gemma3:1b` on 8 GB RAM
- GPU acceleration

## Reproduce this report

A runnable script lives outside this repo at the monorepo root used during testing:
`functional-test.sh` (writes `functional-test-report.md`). Point it at the same
sibling layout and services above.

## Related issues

Install findings were filed as GitHub issues on each family repo (2026-07-04).
Fixes for tirzah Docker, mahalath test import, and Linux README paths are on `main`
as of the same date.