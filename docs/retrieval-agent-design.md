# Retrieval Agent Design (Retrieval v1.2)

Status: Proposed (design). Post-V1. Implements the "dynamic LLM retrieval agent"
requirements and builds on the existing agentic-mode primitives
(`run_memory_agent_loop`, `allowed_tool_specs`, the read-only Mongo query layer).
Recorded as ADR-020.

## 1. Goal and scope

Iterative, local-LLM-driven retrieval for a **single user query session** (one
question → one synthesized answer). The agent gathers candidates through safe
Mongo primitives; **Python coarse-ranks and the LLM fine-judges a bounded
shortlist**; useful chunks accumulate in a session-scoped bucket; a final
(optionally stronger) model synthesizes the answer. Designed to stay robust under
small local context windows, prioritising **retrieval quality over latency** —
but with hard caps as the safety net.

## 2. Decided boundaries (the load-bearing principles)

- **Python is the authoritative controller** of state, validation, paging, and
  stopping. The LLM is stateless per call and **never reads files or Mongo
  directly** — it only receives curated input and returns decisions.
- **No super-query fan-out.** Retrieval is bounded per-primitive queries.
- **Session-scoped exclusion only.** Already-returned chunk IDs are excluded
  within the current session; there is **no cross-session exclusion**, and no
  stored-retrieval "reuse" that influences future retrieval (see §8).
- **Mongo is authoritative; files are optional** audit/export (config).
- **Final synthesis reads the actual kept chunks**, never the running summary.
- **Long-term patterns are human-endorsement-gated** and never auto-written.

## 3. Query primitives (the fixed menu)

A small, fixed set of named, validated primitives — the LLM selects among these;
it does not compose raw filters/projections.

| Primitive | Backed by | Purpose |
|---|---|---|
| `semantic_search` | `embedding_candidate_nodes` | vector/profile similarity |
| `keyword_search` | `search_nodes` | lexical + label/endorsement scoring |
| `hybrid_search` | both, merged + reranked in Python | default broad retrieval |
| `adjacent_context` | `node_context` / tree edges | neighbours of a known node |
| `graph_traverse` | tree/graph edges | follow relationships from a focus node |
| `ontology_lookup` | label/term lookup | resolve a human term to nodes |

Each primitive has a strict JSON arg schema. Python validates every plan as
hostile input, with one bounded repair retry before falling back to a safe
default (e.g. degrade to direct retrieval).

## 4. The loop (Python-orchestrated)

1. **Plan.** The agent model emits a structured plan in one call: chosen
   primitive + args + rationale (+ optional confidence). Plan and reflection are
   combined to conserve LLM calls.
2. **Execute + gate (Python).** Validate, run the primitive, apply the
   session exclusion set, then **coarse-rank/relevance-gate** the result set and
   take the top-K **shortlist**. The expensive per-chunk LLM judgement only ever
   sees a bounded, pre-ranked shortlist — never the raw result set.
3. **Triage (paged, stateless LLM).** Python pages the shortlist to the LLM:
   `[running narrative summary] + [useful-so-far IDs/descriptors] + [this page]`
   → the LLM returns keep/drop per chunk + a summary delta. Python owns the
   cursor/progress; the LLM holds no file/page state.
4. **Record (Python → Mongo).** Kept chunk IDs + metadata go to the session
   **useful-chunks bucket**; the exclusion set and deterministic signals
   (novelty, diminishing returns) update.
5. **Decide continue/stop (Python).** Deterministic signals are primary (§7);
   LLM confidence is advisory; `max_iterations` is the hard cap.
6. **Synthesize.** On stop, the synthesis model reads the **useful-chunks
   bucket (full or referenced chunks)** and produces the answer. The running
   summary is supplementary context only.

## 5. State (Mongo, session-scoped)

- `retrieval_sessions` — plan history, deterministic signals, status.
- `retrieval_useful_chunks` — kept chunk IDs + metadata (the synthesis input).
- exclusion IDs (in the session record).
- session notes — narrative running summary, findings, open questions.

All session state lives outside the LLM context and survives restarts. An
**optional** verbose file export (config: `retrieval_audit_log: off | verbose`)
mirrors this for development/audit; Mongo remains authoritative.

## 6. Models (tiered, all behind the adapter boundary — ADR-004)

- **`agent_model`** (local, configurable) drives planning + triage.
- **`synthesis_model`** (configurable) writes the final answer; **may be a
  frontier model** via an answer adapter. Its role is **only** to (a) provide a
  **larger context window** so more of the useful-chunks bucket fits into the
  synthesis prompt, and (b) produce the **best-quality final answer**. It has
  **no part in retrieval, triage, or selection.** Cloud use is strictly opt-in and
  off by default (local-first). *Note: a frontier answer adapter does not exist
  yet and is a prerequisite build item.*
- **Optional per-task local model override** — the agent may request a different
  local model (e.g. gemma → qwen/mistral) for a sub-goal such as
  language-specific work. Selection is **per-task/coarse, not per-call**, because
  a model swap forces a VRAM reload on the target hardware.

Because synthesis sees only what the **local agent** selected, the chunks that
reach the answer are gated by the agent's triage — so the Python pre-rank (§4.2)
is the real retrieval-quality lever; the frontier model only writes a better
answer over a fuller prompt of the already-selected chunks.

## 7. Context management and stopping

- Python tracks token usage **deterministically**; the LLM is never relied on to
  self-detect truncation or report calibrated confidence.
- Summarisation is a **compromise under context pressure only** — with a larger
  window, full or minimally compressed chunks are preferred.
- **Stop signals**, in priority order: deterministic **novelty** (share of new vs.
  already-excluded IDs) and **diminishing returns** (score deltas) are
  load-bearing; **coverage of key aspects** is advisory (it depends on an
  LLM-produced aspect list); LLM confidence is advisory; `max_iterations` is the
  hard cap. If confidence stays low at the cap, fall back to a **human-in-the-loop**
  option.

## 8. Stored retrieval IDs and long-term patterns

- **Stored retrieval IDs (§1 of the requirements): audit only.** Previously
  retrieved chunk IDs + semantic descriptors are recorded for cross-reference and
  development inspection. They **do not influence** future retrieval. The verbose
  session log (config) is the expanded form of this.
- **Long-term pattern notes:** a persistent store of successful retrieval
  strategies the agent may reference when planning. New patterns or significant
  changes are added **only after explicit human approval** — never written
  autonomously. Patterns are injected as a small, curated hint block, kept tiny so
  they don't compete with the retrieval context budget.

## 9. Safety and degradation

- Strict Python-side validation of every plan; bounded repair; safe fallback to
  direct retrieval on repeated malformed output.
- Hard caps: `max_iterations`, `shortlist_size`, `page_size`,
  `max_candidates_per_query`.
- Optional transparency in the final output: key sources, traversal path,
  confidence.

## 10. Mahalath integration (forward)

Mahalath's ADR-031 anticipates an opt-in, synchronous "Tirzah context-collation"
call at ingestion, with an open question about whether Tirzah exposes a queryable
store or a "document + term → collated context" call. The primitive menu (§3) —
particularly `ontology_lookup` + `hybrid_search` + `adjacent_context` composed
behind a single collation entry point — is the natural contract. Define it from
both sides rather than retrofitting.

## 11. Config (new `RetrievalConfig` fields, indicative)

- `retrieval_mode`: `direct` | `agentic` | `deep` (this design = `deep`)
- `agent_model`, `synthesis_model` (+ adapter selection)
- `deep_retrieval_max_iterations`, `shortlist_size`, `page_size`,
  `max_candidates_per_query`, `summarize_token_threshold`
- `pattern_notes_enabled`, `retrieval_audit_log: off | verbose`

## 12. Build relationship and non-goals

- **New `deep` retrieval mode** that **reuses** the existing primitives, schema
  validation, and `session_continuity` state; the current `direct` and `agentic`
  modes are unchanged.
- **Non-goals this iteration:** super-query fan-out; cross-session
  exclusion/reuse that influences retrieval; autonomous pattern writes; learned
  weights / RL.

## 13. Open items for the implementation phase

- ~~Per-primitive schemas~~ **Done (skeleton):** the fixed validated primitive
  menu is `retrieval/deep.py` (`PRIMITIVES`, `validate_primitive_call`,
  `run_primitive`, `allowed_primitive_specs`) — keyword/hybrid/adjacent/graph,
  each delegating to the read-only query functions. Deep bounds added to
  `RetrievalConfig` (`deep_max_iterations/_max_candidates/_shortlist_size/_page_size`).
  **`semantic_search` is now wired for free-text queries** (`query_embedding_candidate_nodes`
  in `retrieval/queries.py`): a pure meaning-based primitive that ranks embedded
  nodes by cosine similarity to a per-call query embedding — reaching nodes that
  match by meaning even with **no shared keywords** (unlike `keyword_search`/
  `hybrid_search`, which filter lexically first). A per-call `embedder` seam lets
  the planner embed a focused phrase, not just the original question; it degrades
  to empty (planner falls back to lexical) when hybrid is off / the adapter is mock.
  The **`deep` mode is complete and selectable** (deep.py + interaction.py):
  `run_deep_retrieval` + `DeepRetrievalSession` (loop + stop signals),
  `make_planner`/`make_triager` (real LLM seams over the answer adapter),
  `synthesize_answer` + `run_deep_answer` (retrieve → synthesise), wired via
  `answer_query_deep` (`retrieval_mode == "deep"`; CLI `--retrieval-mode deep`,
  web mode list). Opt-in; mock-gated query embedding for the hybrid primitive.
  Remaining nice-to-haves: a token estimator (summarisation under pressure), a
  frontier `synthesis_model` adapter, and flipping defaults after a real-corpus +
  real-embedding smoke (needs live Mongo/Ollama).
- ~~The coarse-rank/relevance-gate function (the hybrid lexical+vector reranker).~~
  **Done (building block):** `hybrid_rank` + `merge_candidate_pools` in
  `retrieval/queries.py` — deterministic gate (lexical OR vector floor) + min-max
  normalised lexical blended with vector similarity, component scores exposed.
  **Wired into both live retrieval modes** via an optional `query_embedding` on
  `search_nodes` — the agentic `search_nodes` tool and direct-mode focus selection
  (`ranked_focus_matches`) — opt-in `runtime.hybrid_search_enabled`, active only
  with a real, non-mock embedding adapter; degrades to lexical otherwise.
  **`runtime.hybrid_search_enabled` now defaults on** (2026-06-18) after the
  real-corpus validation — harmless under the mock adapter, active with a real one.
- A token estimator good enough to drive summarisation thresholds.
- The frontier answer adapter for `synthesis_model`.
- Concrete `coverage` heuristic (advisory).
