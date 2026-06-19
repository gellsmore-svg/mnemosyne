---
type: Concept
title: Governance
description: Agent identities, process objects, governance policies, process runs, and trust-weighting profiles are seedable and inspectable; in V1 governance is observational — it informs and records but does not yet enforce steps or affect default ranking.
resource: https://github.com/gellsmore-svg/tirzah/blob/main/src/tirzah/db/governance.py
tags: [tirzah, governance, trust, identities, observational]
timestamp: 2026-06-19T00:00:00Z
---

# Governance

Tirzah models *who* and *under what process* memory is produced and used:

- **Agent identities** — actors that ingest, answer, or endorse; retrieval can be
  scoped by identity.
- **Process objects & process runs** — declared processes and their executions
  (`start-process-run` / `update-process-run`), recorded for provenance.
- **Governance policies** — declared expectations over processes.
- **Trust-weighting profiles & diagnostics** — trust/temporal signals
  (`retrieval/trust.py`) exposed for inspection (`trust-diagnostic`).

**In V1 governance is observational** (`docs/v1-known-limitations.md`): identities,
process objects, policies, runs, and trust profiles are seedable and inspectable,
but process steps and approvals are **not automatically enforced**, and trust/
temporal diagnostics **do not yet affect default ranking**. This is the seam where
endorsement-gated memory (see [sessions & continuity](sessions-and-continuity.md))
and future enforcement will land. Lives in the [storage module](../modules/storage.md)
(`db/governance.py`) and the [governance/sessions CLI](../cli/governance-sessions.md).
