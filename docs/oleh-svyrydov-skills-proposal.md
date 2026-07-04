# Proposal: Adapt Oleh Svyrydov's AI Development Team Skills for Tirzah

**Status:** Draft proposal
**Date:** 2026-06-22
**Source reviewed:** `olehsvyrydov/AI-development-team`, local clone at `/home/cello/domains/AI-development-team`
**Target project:** Tirzah, especially retrieval, ingestion, semantic precision, and Noa-managed integration with Mahalath and Hoglah

## Executive Summary

Oleh Svyrydov's AI Development Team repository is not mainly an application runtime. Its value is a portable set of agent "skills", commands, workflow gates, templates, and install conventions that make an AI coding assistant behave more like a structured team than a single general-purpose helper.

For Tirzah, the useful part is not the persona names or a wholesale imported team roster. The useful part is the discipline:

- classify work before acting;
- route specialist tasks to the right operating mode;
- require evidence before marking work complete;
- record handoffs and gate decisions in a simple ledger;
- keep role guidance small, with deeper references loaded only when needed.

Tirzah already has the right engineering shape for this: Python is the authoritative controller, the LLM is bounded by validated tool surfaces, source provenance is load-bearing, and long-term pattern writes are human-gated. A Tirzah-specific skill layer could make those principles easier for agentic coding tools and future operators to apply consistently.

Recommended direction: adapt the pattern, not the package. Build a small Tirzah skill pack containing a few domain-specific skills: retrieval auditor, ingestion reviewer, semantic-precision reviewer, graph-governance reviewer, and release verifier. Pair those with a lightweight workflow ledger and do not let the skills bypass Python-side validation, tests, or human endorsement.

## What Is a "Skill" in Agentic Land?

A skill is a reusable instruction module that tells an AI assistant how to behave for a specific kind of task.

It is not usually "code" in the normal sense. It is closer to a role-specific operating manual that an agent can load when the task matches its description. A good skill usually contains:

- when to use it;
- what it is responsible for;
- what it must not do;
- what files, tools, or APIs it should inspect;
- what evidence it must collect;
- what output format it should produce;
- what handoff or approval gate must happen next.

In practical terms:

- A normal prompt says: "Help me improve retrieval."
- A retrieval skill says: "When improving retrieval, inspect query primitives, ranking traces, provenance, budgets, tests, and regression fixtures; do not change trust semantics silently; produce a before/after evidence report."

That distinction matters. A skill can make repeated agent work more consistent because the assistant does not have to rediscover the operating rules every session. It also makes the rules inspectable by humans.

Skills are still weaker than executable controls. They guide the agent, but they do not enforce correctness by themselves. Tirzah should treat skills as a human-readable process layer above hard code constraints, tests, schemas, and review gates.

## Why This Could Benefit Tirzah Users

### 1. More Reliable Agentic Maintenance

Tirzah has subtle boundaries: the LLM plans but does not directly read Mongo or files; Python validates tool calls; provenance is preserved at chunk level; graph relationship writes are review-gated. These are easy for a new assistant session to violate unless repeatedly restated.

Tirzah-specific skills could encode these boundaries once and make every future coding session start from the same contract.

Expected user benefit: fewer regressions where an assistant adds clever-looking shortcuts that weaken source authority, trust gating, or local-first constraints.

### 2. Clearer Workflows for Complex Retrieval Changes

Retrieval improvements are not simple feature changes. They affect ranking, context construction, user trust, source attribution, and answer quality. Oleh's proportional workflow model is useful here: small doc or test updates should stay lightweight; changes to retrieval ranking, semantic graph writes, or answer synthesis should trigger heavier gates.

Example Tirzah adaptation:

- trivial: docs typo, no gate beyond self-review;
- small: one bounded bug fix, run focused tests;
- standard: retrieval behavior change, run regression fixtures and inspect traces;
- significant: graph endorsement, semantic precision, ingestion promotion, or external model adapter change, require design review and verification.

Expected user benefit: faster small fixes, stricter review for changes that can distort memory.

### 3. Better Evidence Before "It Works" Claims

The most useful skill in Oleh's repo is the `verify` pattern: adversarial review, exact findings, traceability, placeholder detection, and concrete proof before passing a gate.

Tirzah users need this because retrieval quality can appear to work on one query while failing structurally. A Tirzah verifier should require:

- source document IDs used in answer context;
- node IDs and provenance for selected chunks;
- ranking trace before and after change;
- budget/skipped-context explanation;
- test fixture or real-corpus smoke;
- evidence that Mahalath semantic labels are used when enabled, not merely configured.

Expected user benefit: fewer false positives from demos that only prove installation, not actual semantic behavior.

### 4. Stronger Mahalath-Tirzah Semantic Seam

The Noa reinstall now proves the seam can work: Mahalath is injected into Tirzah's environment, Tirzah config enables Mahalath, and the semantic smoke shows Tirzah's prompt builder using a live MPL sense.

A dedicated "semantic precision" skill could preserve that standard:

- verify `mahalath_enabled`;
- verify config points to the intended Mongo DB;
- seed or inspect known labels;
- run an A/B prompt comparison;
- fail if the semantic-on prompt does not differ from semantic-off;
- report whether labels came from live Mahalath ontology or a fallback resolver.

Expected user benefit: users can trust that "thinking in Mahalath terms" is operational, not aspirational.

### 5. Easier Onboarding for Future Contributors

Tirzah has many documents: requirements, retrieval design, process docs, reviews, roadmap, and known limitations. Skills can act as a routing layer:

- retrieval change? Load retrieval skill and its references.
- ingestion change? Load ingestion skill and source-fidelity rules.
- governance change? Load graph/trust skill.
- release candidate? Load verifier skill.

Expected user benefit: new contributors and AI assistants spend less time guessing which standards apply.

## Proposed Tirzah Skill Pack

Do not import all 29 skills from AI Development Team. Start with five Tirzah-specific skills.

### 1. `tirzah-retrieval-engineer`

Purpose: guide changes to direct, agentic, and deep retrieval.

Must enforce:

- Python remains authoritative for validation, state, paging, and stopping.
- LLM planners return structured decisions only.
- final synthesis reads kept chunks, not only summaries.
- retrieval changes must expose trace diagnostics.

Useful references:

- `docs/retrieval-agent-design.md`
- `docs/agentic-retrieval-process.md`
- `src/tirzah/retrieval/`
- retrieval tests and fixtures.

### 2. `tirzah-ingestion-reviewer`

Purpose: protect source fidelity and transactional ingestion.

Must enforce:

- raw source text is preserved;
- candidate chunking or summaries are derived, never source authority;
- rebuilds and promotions are auditable;
- failed items go through dead-letter handling.

Useful references:

- `docs/improvements-and-enhancements.md`, section 1;
- `src/tirzah/ingestion/`;
- `src/tirzah/db/repositories.py`.

### 3. `tirzah-semantic-precision-reviewer`

Purpose: verify the Mahalath seam and ontology-conditioned prompting.

Must enforce:

- semantic labels come from live Mahalath when configured;
- fallback resolver is clearly labelled as fallback;
- semantic-on and semantic-off behavior differ in the prompt envelope;
- strict mode behavior is explicit.

Useful checks:

- Noa semantic smoke;
- `tirzah.semantic`;
- `build_prompt_envelope`;
- config fields `mahalath_enabled`, `mahalath_mongo_uri`, `mahalath_mongo_db`, `mahalath_strict`.

### 4. `tirzah-graph-governance-reviewer`

Purpose: protect trust, endorsement, temporal, and semantic-edge rules.

Must enforce:

- no autonomous promotion of trust or long-term retrieval patterns;
- graph edges that change retrieval meaning require review evidence;
- ranking changes involving trust or time are opt-in, bounded, and explainable.

Useful references:

- `docs/governance-schema-plan.md`;
- `docs/improvements-and-enhancements.md`, section 3;
- semantic edge queues and graph traversal code.

### 5. `tirzah-release-verifier`

Purpose: final pre-release or pre-merge audit.

Must require:

- focused tests for touched behavior;
- at least one realistic retrieval/semantic smoke for relevant changes;
- config migration notes if config keys changed;
- changelog or known-limitations update when user-visible behavior changed;
- explicit residual-risk section.

This is the closest equivalent to Oleh's `/verify`, but narrowed to Tirzah.

## Workflow Proposal

Add a small workflow definition for Tirzah rather than adopting the whole AI Development Team workflow.

Suggested gates:

| Gate | Trigger | Owner skill | Hard/soft |
|---|---|---|---|
| `RETRIEVAL_BEHAVIOR_REVIEWED` | ranking, query, prompt envelope, deep retrieval | `tirzah-retrieval-engineer` | hard |
| `SOURCE_FIDELITY_REVIEWED` | parser, ingestion, rebuild, archive, chunking | `tirzah-ingestion-reviewer` | hard |
| `SEMANTIC_SEAM_VERIFIED` | Mahalath integration, MPL labels, semantic prompt behavior | `tirzah-semantic-precision-reviewer` | hard |
| `GRAPH_GOVERNANCE_REVIEWED` | semantic edges, trust weighting, endorsement, pattern memory | `tirzah-graph-governance-reviewer` | hard |
| `RELEASE_VERIFIED` | release, install, broad behavior change | `tirzah-release-verifier` | hard |
| `DOCS_UPDATED` | user-visible behavior changed | technical writer or release verifier | soft-to-hard for releases |

Use a file ledger such as `.tirzah-workflow-state.json` or a docs-local audit report at first. Do not introduce a service dependency for this.

## How Oleh's Skills Should Be Improved Before Use

### 1. Replace Generic Personas with Domain Contracts

The AI Development Team skills sometimes spend many lines defining personas and broad responsibilities. Tirzah does not need a fictional product owner or named team member. It needs short, enforceable domain contracts.

Improvement: convert each adopted skill into a concise checklist plus references, not a long role biography.

### 2. Separate Advice from Gates

Some skills mix "good advice" with mandatory workflow gates. Tirzah needs clear labels:

- mandatory invariant;
- recommended practice;
- optional heuristic;
- experiment.

Improvement: every skill should mark which rules are load-bearing and which are advisory.

### 3. Make Evidence Requirements Executable Where Possible

Skills alone cannot prove correctness. Tirzah should pair each skill with commands or tests:

- healthcheck;
- semantic smoke;
- retrieval fixture test;
- source-fidelity diff;
- graph-governance queue inspection.

Improvement: each skill should include a "verification commands" section that can be run directly.

### 4. Avoid Context Bloat

Oleh's best structural idea is progressive disclosure: small `SKILL.md`, deeper references only when needed. Tirzah should be stricter than the source repo here because retrieval work is already context-heavy.

Improvement: keep each Tirzah skill under roughly 150-250 lines and push deep examples into references.

### 5. Fix "Enforced" Language

The AI Development Team README says gates are enforced, but most are enforced by instruction unless an optional workflow backend exists. Tirzah should be more precise.

Improvement: say "agent-guided gate" when the control is prompt-level, and "runtime-enforced gate" only when Python, tests, schemas, or CI actually block progress.

### 6. Add Tirzah-Specific Regression Fixtures

The adopted skills should not merely say "check retrieval quality". They should name fixture classes:

- ambiguous term resolved by Mahalath;
- large document with skipped context;
- lexical miss recovered by semantic search;
- graph traversal with reviewed vs unreviewed edge;
- stale or superseded source tree not promoted.

Improvement: each skill should point to at least one fixture or smoke script.

## Risks and Mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Process theatre | More roles can create busywork without better retrieval. | Keep only five Tirzah-specific skills; require evidence outputs. |
| False enforcement | Agent instructions can be ignored. | Pair skills with tests, scripts, and Python-side invariants. |
| Context overhead | Long skills compete with retrieval/design context. | Use progressive loading and concise skill files. |
| Generic advice | Broad software-agent roles may miss Tirzah's real risks. | Rewrite around Tirzah's actual architecture and docs. |
| User confusion | "Skills" can sound magical. | Document them as reusable operating manuals, not autonomous workers. |

## Implementation Plan

### Phase 1: Documentation-only skill pack

Create `skills/` or `.claude/skills/` content for Tirzah with the five proposed skills. They should not change runtime behavior. Use them manually in coding sessions and evaluate whether they improve review quality.

Exit criteria:

- each skill has a trigger, responsibilities, non-goals, references, and verification commands;
- at least one real Tirzah task uses a skill and produces a useful audit note.

### Phase 2: Workflow ledger

Add a lightweight `.tirzah-workflow-state.json` or per-change audit report template. Record only gates that matter for the change.

Exit criteria:

- a retrieval behavior change records `RETRIEVAL_BEHAVIOR_REVIEWED`;
- a semantic seam change records `SEMANTIC_SEAM_VERIFIED`;
- no mandatory Jira, dashboard, or service dependency.

### Phase 3: Test-backed gates

Attach the skills to runnable checks:

- semantic seam smoke;
- retrieval regression fixture;
- source-fidelity ingestion smoke;
- release verifier checklist.

Exit criteria:

- release verification can be repeated by a new assistant session;
- failure output names the missing evidence.

### Phase 4: Optional packaging

Only after the skills prove useful, package them for Claude Code/Cursor/Codex-style use. Avoid installing broad AI Development Team skills globally unless a project explicitly opts in.

## Recommendation

Adopt the pattern selectively.

Oleh's repository is valuable because it turns repeated agent work into named, inspectable workflows. Tirzah should borrow the workflow discipline, verification posture, progressive skill loading, and file-ledger idea. It should not inherit the full generic team structure or rely on prompt-level gates as if they were hard enforcement.

The highest-value first slice is `tirzah-semantic-precision-reviewer` plus `tirzah-release-verifier`, because the Noa reinstall already gives us a concrete acceptance test: Tirzah must demonstrate that semantic-on prompting uses live Mahalath MPL labels.
