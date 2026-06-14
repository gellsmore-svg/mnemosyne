# Contributing

Tirzah is currently a fast-moving local prototype. Keep changes small, tested, and tied to the project roadmap in `docs/build-roadmap.md`.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Some tests exercise real MongoDB collections. With MongoDB available at the
configured URI, run that profile directly:

```bash
.venv/bin/python -m pytest -m real_mongo
```

Use `config.example.yaml` as the checked-in template and keep machine-local settings in ignored `config.yaml`.

## Pull Requests

- Describe the behavior change and the affected CLI/API/UI surface.
- Include tests for ingestion, retrieval, or interaction changes when practical.
- Avoid committing local source archives, dead-letter files, prompt history exports, or secrets.
