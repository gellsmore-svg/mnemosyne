---
type: Concept
title: Sessions & continuity
description: Conversations persist as sessions of exchanges with their used nodes and context metadata; active documents track the working set; each exchange records a database-backed restart-state snapshot that can be inspected or re-rendered.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/sessions/continuity.py
tags: [tirzah, sessions, continuity, restart-state]
timestamp: 2026-06-19T00:00:00Z
---

# Sessions & continuity

Tirzah persists the working state of a conversation, not just answers:

- **Sessions & exchanges** — each `ask`/`chat` is an **exchange** saved in a
  **session**, with its used node ids, the answer, and context metadata (included
  + skipped chunks).
- **Active documents** — the working set of documents in focus for a session,
  tracked across exchanges.
- **Session continuity / restart state** — each exchange records a database-backed
  **restart-state snapshot** (a first-class prompt-iteration record) capturing the
  considered context, including a bounded summary of skipped chunks. It can be
  inspected (`session-continuity`) or re-rendered (`restart-render`) on demand.
- **Generated-output review & endorsement** — model output and semantic-edge
  candidates can be ingested back under review and explicitly **endorsed**
  (`endorse-node`, `review-generated-output`), gating what becomes trusted memory.

These records live in the [sessions module](../modules/sessions.md) and are surfaced
in the [web](../modules/web.md) Ask workspace and the
[CLI](../cli/governance-sessions.md). Full final-context display and follow-up
seeding remain post-V1.
