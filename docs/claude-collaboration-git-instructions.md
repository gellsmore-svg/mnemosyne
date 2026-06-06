# Claude Collaboration: Local Repo And GitHub

Status: operator handoff note for Claude-assisted development.

## Local Repository

- Work as the `cello` user environment, not root.
- Repo path: `/home/cello/domains/Mnemosyne`
- Primary branch: `main`
- GitHub remote: `https://github.com/gellsmore-svg/mnemosyne.git`

Start every coding or review session with:

```bash
cd /home/cello/domains/Mnemosyne
git status --short
git branch --show-current
git remote -v
```

If there are uncommitted changes, treat them as user or collaborator work unless the current task clearly owns those files. Do not revert, reset, or overwrite unrelated changes.

## GitHub Access

Use the existing Git/GitHub authentication already configured in the `cello` environment.

Safe checks:

```bash
git remote -v
git fetch --dry-run
git status --short
git log -5 --oneline
```

If `gh` is available, this is also safe:

```bash
gh auth status
```

Do not print, inspect, copy, request, or store GitHub tokens. Do not run commands intended to reveal credentials, credential helpers, environment secrets, or token files.

## Sync Workflow

Before making changes:

```bash
git status --short
git fetch origin
git log --oneline --decorate --max-count=5
```

After changes:

```bash
git status --short
git diff --stat
```

Run the smallest relevant tests first, then the full suite when the change affects shared behavior:

```bash
.venv/bin/pytest -q
```

Commit only files relevant to the task:

```bash
git add <changed-files>
git commit -m "<clear imperative message>"
git push
```

## Current Product Language Rule

Use product terms before implementation details.

Examples:

- Use `text similarity profile` for the product element.
- Use `embedding vector` only for the current technical representation.
- Use `source document`, not `chunk`, when referring to the human/domain artifact.
- Use `semantic relationship`, not `graph edge row`, when referring to the product element.
- Use `agent identity`, not `prompt text`, when referring to the product element.
- Use `process obligation`, not `checklist item`, when referring to the product element.
- Use `memory state`, not `chat history`, when referring to the product element.
- Use `trust assessment`, not `numeric score`, when referring to the product element.

## Current Architectural Boundary

HTTP is allowed for:

- the human web UI;
- optional final hosted answer-model calls.

HTTP is not allowed for:

- ingestion;
- retrieval memory operations;
- memory-agent tool orchestration;
- Python memory tools;
- repository text similarity profile generation.

The compliant profile adapter bridge is `runtime.embedding_adapter: local_command`, which calls `runtime.profile_command` over stdin/stdout JSON. The actual local profile generator still needs to be chosen, installed, and quality-tested.

## Useful Status Checks

Local web app:

```bash
curl -s http://127.0.0.1:8765/api/runtime
curl -s 'http://127.0.0.1:8765/api/ingestion/status?limit=2'
```

Expected local URL:

```text
http://127.0.0.1:8765/?display=epaper
```

## Review Expectations

For code review, prioritize:

- behavioral bugs;
- requirement drift;
- missing tests;
- unsafe transport assumptions;
- unclear operator-facing logs;
- product vocabulary drift;
- accidental changes to source preservation, retrieval authority, or agent autonomy.

For implementation help, keep changes small, tested, and easy to review. Avoid broad refactors unless explicitly requested.
