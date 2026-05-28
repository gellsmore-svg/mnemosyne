# Governance Schema Plan

Status: planning contract. Do not treat this as implemented runtime behavior until the relevant collections, migrations, APIs, and tests exist.

This document translates the Mnemosyne cognitive architecture draft into concrete schema surfaces for Mnemosyne. The immediate purpose is to keep identity, trust, temporal weighting, and process enforcement explicit before granting memory agents any write autonomy.

## Design Constraints

- Memory-agent tool calls remain read-only until identity, trust, and process rules can constrain writes.
- Reviewed semantic edges remain operator-governed; automated relationship extraction should produce candidates, not trusted graph writes.
- Schema additions must preserve source provenance and endorsement labels at node/edge granularity.
- Governance fields should be queryable and auditable, not hidden only inside unstructured prompt text.
- Names below are candidate schema names; a future product rename is expected but not yet chosen.

## Candidate Collections

### `agent_identities`

Purpose: define agent-specific semantic scope, access, weighting, and process obligations.

Core fields:

- `identity_id`: stable string key, unique.
- `title`: human-readable name.
- `kind`: `shared`, `domain`, `restricted`, `moderator`, or `composite`.
- `description`: concise purpose.
- `trusted_labels`: labels this identity may prefer.
- `excluded_labels`: labels this identity must avoid.
- `trusted_document_ids`: optional exact document allow-list.
- `excluded_document_ids`: optional exact document deny-list.
- `trusted_tree_ids`: optional exact tree allow-list.
- `excluded_tree_ids`: optional exact tree deny-list.
- `allowed_relation_types`: graph relation types visible to this identity.
- `excluded_relation_types`: graph relation types hidden from this identity.
- `weighting_profile_id`: reference to a temporal/trust weighting profile.
- `required_process_ids`: process objects that must be acknowledged or followed.
- `governance_policy_ids`: higher-level policies constraining the identity.
- `created_at`, `updated_at`.

Initial use:

- Inject identity summaries into memory-agent prompts.
- Scope retrieval and graph traversal by identity.
- Record which identity was active during an exchange.

### `governance_policies`

Purpose: represent constitutional, policy, and process authority layers.

Core fields:

- `policy_id`: stable string key, unique.
- `title`.
- `authority_layer`: `constitutional`, `governance_policy`, `process_rule`, `domain_rule`, `task_instruction`, or `session_prompt`.
- `priority`: numeric tie-breaker within a layer.
- `text`: policy content.
- `applies_to_identity_ids`.
- `applies_to_labels`.
- `conflict_behavior`: `override`, `block`, `warn`, or `require_approval`.
- `review_status`: `draft`, `active`, `deprecated`, or `rejected`.
- `created_at`, `updated_at`, `last_reviewed_at`.

Initial use:

- Keep higher-authority rules visible to the planner/final answer prompt.
- Provide an auditable basis for refusing unsafe or out-of-process agent actions.

### `process_objects`

Purpose: turn processes into enforceable semantic objects rather than passive docs.

Core fields:

- `process_id`: stable string key, unique.
- `title`.
- `description`.
- `steps`: ordered list with `step_id`, `title`, `required`, `validation`, and `approval_required`.
- `acknowledgement_required`: boolean.
- `exception_allowed`: boolean.
- `exception_policy_id`: optional policy reference.
- `audit_required`: boolean.
- `applies_to_identity_ids`.
- `applies_to_labels`.
- `created_at`, `updated_at`.

Initial use:

- Track where a session or agent is inside a workflow.
- Let agents propose exceptions without silently bypassing required steps.

### `process_runs`

Purpose: audit one execution of a process.

Core fields:

- `run_id`: stable string key, unique.
- `process_id`.
- `session_id`.
- `identity_id`.
- `status`: `pending`, `active`, `completed`, `blocked`, `exception_requested`, or `abandoned`.
- `current_step_id`.
- `completed_steps`: list of step ids plus timestamps.
- `exceptions`: list of exception proposals, decisions, reviewer, note, and timestamp.
- `exchange_ids`: related exchanges.
- `created_at`, `updated_at`.

Initial use:

- Persist process position for restart and continuity.
- Support moderator review of procedural compliance.

### `trust_weighting_profiles`

Purpose: configure dynamic trust and temporal decay by domain or identity.

Core fields:

- `weighting_profile_id`: stable string key, unique.
- `title`.
- `recency_importance`: 0.0 to 1.0.
- `frequency_importance`: 0.0 to 1.0.
- `stability_importance`: 0.0 to 1.0.
- `context_sensitivity`: 0.0 to 1.0.
- `verification_importance`: 0.0 to 1.0.
- `default_decay_half_life_days`: optional numeric.
- `created_at`, `updated_at`.

Initial use:

- Make decay behavior explicit before applying it to retrieval ranking.
- Distinguish stable processes from time-sensitive facts.

## Node And Edge Field Extensions

### Nodes

Candidate additions to node records or `metadata.governance`:

- `identity_visibility`: identity ids allowed to see/use the node.
- `identity_exclusions`: identity ids denied access.
- `trust_score`: 0.0 to 1.0, distinct from endorsement label.
- `sensitivity`: `public`, `internal`, `restricted`, or `isolated`.
- `temporal_profile_id`: reference to a trust weighting profile.
- `last_verified_at`.
- `verification_required`: boolean.
- `process_tags`: process ids or process-related labels.
- `authority_layer`: optional authority marker for constitutional/process content.

### Graph Edges

Candidate additions:

- `trust_score`: 0.0 to 1.0.
- `identity_visibility`.
- `identity_exclusions`.
- `temporal_profile_id`.
- `last_verified_at`.
- `valid_from`, `valid_until`.
- `authority_layer`.
- `review_status`: `candidate`, `reviewed`, `deprecated`, or `rejected`.

## Retrieval Implications

Retrieval should eventually apply this order:

1. Hard exclusions from identity and sensitivity.
2. Authority-layer constraints.
3. Endorsement and rejection filtering.
4. Trust and verification weighting.
5. Temporal weighting.
6. Semantic graph and lexical relevance.
7. Usage and continuity boosts.

The current system only implements pieces of steps 3, 6, and 7. Reviewed semantic-edge diagnostics are a bridge toward explaining step 6.

## Implementation Sequence

1. Add schema constants and index planning for identity, policy, process, and weighting profile collections. Implemented for indexes and default seeds.
2. Add read-only CLI/API listing and exact lookup for those collections. Implemented.
3. Seed one shared identity and one default trust weighting profile. Implemented.
4. Include active identity summaries in memory-agent prompts. Implemented.
5. Add retrieval filters for identity exclusions before any weighting changes. Implemented for the agentic search tool path; direct retrieval, proximity expansion, graph paths, and broader policy enforcement remain open.
6. Add process-run persistence for restart/continuity state. Implemented as explicit helper/CLI/API persistence for process starts, progress updates, exchange links, and exception proposals; answer requests automatically create/update `answer_query` process runs and mark retrieval/planning/adapter/save failures blocked, while automatic rule enforcement remains open.
7. Add trust/temporal weighting diagnostics before changing retrieval ranking. Implemented as read-only per-node diagnostics exposed through CLI/API and compact retrieval traces; Mongo naive datetimes are normalized for temporal decay, and retrieval ranking effects remain open.
8. Only after those constraints exist, consider narrowly scoped agent write proposals.

## Open Questions

- Decide on a future product name before renaming user-facing identity/governance surfaces.
- Decide whether visibility rules live directly on nodes/edges, as separate ACL documents, or both.
- Decide whether trust score should be manually reviewed only, model-assisted, or computed from provenance and usage signals.
- Decide how moderator agents are represented: identity only, process role, or separate policy evaluator.
- Decide how much of `.restart.md` should be rendered from `process_runs` and continuity-state records.
