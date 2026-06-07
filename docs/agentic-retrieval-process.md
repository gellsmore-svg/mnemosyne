# Agentic Retrieval Process

Last updated: 2026-05-21

This document describes the implemented agentic retrieval and answer path in Tirzah. The current scaffold uses Python as the runtime substrate for tool execution, validation, budgeting, prompt assembly, provenance, and persistence. The intended product direction is that the memory-agent/controller owns the contextual strategy, while Python enforces the interface contract and keeps repository writes, budgets, provenance, and safety constraints reliable.

## Runtime Roles

Tirzah currently separates two LLM-facing roles:

- Memory-agent LLM: plans retrieval. It must not answer the user. It returns JSON tool calls or a done decision.
- Final answer LLM: answers the user from the Tirzah tool results and rendered source context.

Both roles are called through the adapter boundary in `tirzah.adapters.answer`. The final answer role may use `mock`, `ollama_cli`, or `ollama_http`; the memory-agent retrieval planner must use a local non-HTTP adapter such as `mock` or `ollama_cli`.

## Entry Point

All ask flows enter `answer_query()`.

Tirzah records an initial `process_trace` item:

- step: `user_prompt`
- input: query, optional focus node, session, requested adapter/model, retrieval mode
- output: submitted prompt text

Runtime overrides are then applied. If `retrieval_mode` is `agentic`, Tirzah calls `answer_query_agentic()`. Otherwise it uses the direct focus-node retrieval path.

## Agentic Orchestration

`answer_query_agentic()` runs in this sequence:

1. Call `run_memory_agent_loop()` to gather retrieval tool results.
2. Call `build_agentic_answer_envelope()` to package those results for the final answer model.
3. Call the final answer adapter.
4. Save the exchange, prompt metadata, used node IDs, and process trace.
5. Return the answer payload and trace.

The final answer model is not called until after the memory-agent loop has stopped.

## Memory-Agent Prompt

Each memory-agent iteration is built by `build_memory_agent_prompt()`.

Python injects:

- memory-agent role instruction;
- a hard instruction not to answer the user;
- required JSON response shapes;
- allowed tools and their argument shapes;
- planner rules, including the three-tool-call cap;
- optional `focus_node_id`;
- current `session_id`;
- compact active document summaries for the session;
- compact active identity summaries for governance context;
- deterministic query assembly guidance;
- summarized prior memory-agent iterations;
- the original user prompt.

The required planner output is one of:

```json
{"status":"continue","tool_calls":[{"tool":"search_nodes","arguments":{"query":"...","limit":5}}]}
```

or:

```json
{"status":"done","tool_calls":[],"compiled_context_notes":"why the gathered context is sufficient or limited"}
```

## Query Assembly

Before the memory-agent prompt is rendered, Python builds a deterministic query assembly with `build_query_assembly()`.

For example, for:

```text
What does the Tirzah technical design say the system is for?
```

Python derives:

- lexical terms: `Tirzah`, `technical`, `design`, `system`
- exact phrases: `Tirzah technical`, `technical design`, `design system`
- named anchors: `Tirzah`
- near-match terms: bounded typo/near-token candidates when an initial search misses and a comparison vocabulary is available
- suggested fallback searches: phrase probes first, then individual lexical terms

This gives the memory-agent stable search handles without letting it invent or lose the user's substantive terms.

## Memory-Agent Iteration

For each iteration, Python:

1. Builds the memory-agent prompt.
2. Appends a `memory_agent_iteration` trace entry.
3. Calls `answer_adapter(memory_runtime).answer(...)`.
4. Parses the returned text as JSON with `parse_memory_agent_decision()`.
5. Validates tool names and arguments.
6. Executes allowed tool calls in Python.
7. Summarizes the tool results for the next memory-agent iteration.
8. Stops or continues, depending on the planner decision and configured iteration limit.

The trace input records the planner adapter, model, requested response format, thinking controls, prompt text, and allowed tool specs. This is diagnostic metadata only; Python still validates the returned JSON and executes tools itself.

The memory-agent planner/retrieval loop must use local non-HTTP model/tool execution. HTTP is acceptable for the human web interface and may be acceptable for a final hosted answer-model call, but it is not an allowed transport for retrieval orchestration or Python memory tools.

If the planner does not return parseable JSON, Python creates a conservative fallback decision:

```json
{"status":"continue","tool_calls":[{"tool":"search_nodes","arguments":{"query":"<original query>","limit":5}}]}
```

If the planner returns no tool calls before any context has been gathered, Python also forces one fallback search.

If the planner fails after useful tool context already exists, Python stops rather than spending another iteration on a failing planner.

## Tool Execution

The LLM never directly accesses MongoDB or the filesystem. It requests tool calls; Python validates and executes them.

Supported read-only tools are:

- `search_nodes`
- `compile_context`
- `get_node_context`
- `get_document`
- `get_document_tree`
- `get_graph_edges`
- `expand_proximity`
- `expand_graph_paths`
- `semantic_candidates`
- `list_active_documents`
- `list_documents`

`get_document_tree` is a navigation tool. Its listed node IDs help the planner choose a later `get_node_context` or `compile_context` call, but the tree listing alone is not counted as used evidence for node usage scoring.

`get_graph_edges` returns bounded incoming, outgoing, or bidirectional edge records for a known node ID, including compact source/target node previews.

`expand_proximity` ranks one-hop adjacent nodes from graph edges using a deterministic edge score derived from weight and confidence. Python then compiles graph context for the top two adjacent nodes so proximity-expanded evidence can reach the final answer model as source records, not only as a ranked ID list. It is a candidate expansion helper, not the final path-scoring algorithm.

`expand_graph_paths` performs bounded multi-hop graph traversal from a known node ID. Each hop uses the same edge score as proximity expansion, path scores multiply hop scores, cycles back through already visited nodes are skipped, and depth, branch, and result limits cap traversal. Python compiles context for the top two path targets before final answer assembly.

`semantic_candidates` inspects read-only label-overlap candidates for a known node. It excludes structural labels and source-root containers, can optionally include same-document candidates, and compiles context for the top two candidates. It does not write inferred graph edges.

Operator CLI and web/API commands can enqueue these candidates into a pending semantic-edge review queue, accept queued candidates into reviewed graph edges, reject weak candidates with reviewer notes, and create reviewed semantic edges explicitly. Those write operations are outside the memory-agent tool surface.

Structural `contains` edges can be backfilled from existing parent/child node links. These edges provide source-faithful document-tree movement for graph traversal before semantic relation extraction exists. Equal-score structural paths use natural node-key/title ordering so siblings follow document order where possible.

`execute_tool_calls()` wraps every tool result with:

- index;
- tool name;
- arguments;
- success flag;
- output;
- optional diagnostic details;
- optional error.

## Search Tool

`execute_search_nodes_tool()` implements the current search behavior.

It:

1. Normalizes the planner query.
2. Combines planner query and original user query into a ranking query.
3. Builds query assembly from both query surfaces.
4. Calls `search_nodes()` against MongoDB.
5. If no matches are found, augments query assembly with bounded near-match terms from label, document-title, and source-path vocabulary, then tries fallback phrases, near-match candidates, and terms from query assembly.
6. Records fallback probe counts.
7. Deduplicates candidate nodes by `node_id`.
8. Reranks candidates with `score_node_match()`.
9. Keeps the requested top matches.
10. Compiles graph context for the top two matches.

The search details include:

- normalized query;
- ranking query;
- query assembly;
- fallback query probes and result counts.

## Reranking

`score_node_match()` rewards:

- exact phrases in titles;
- exact phrases in text previews;
- lexical terms in titles and previews;
- intent terms such as `system`, `purpose`, `concept`, `function`, and `role`;
- named anchors in title, preview, or source path;
- source section labels.

It penalizes:

- missing named anchors;
- source root nodes;
- document metadata header sections;
- empty or separator-only source chunks.

This prevents broad phrase searches from promoting boilerplate above substantive sections.

## Context Compilation

`compile_context()` builds a role-tagged graph neighborhood around a focus node:

- focus node;
- ancestors;
- nearby siblings;
- descendants;
- document metadata.

Records carry role, distance, labels, endorsement label, provenance, node IDs, titles, and source text or previews.

## Planner History Summary

After tool execution, Python summarizes results for possible later memory-agent iterations with `summarize_tool_results_for_memory_agent()`.

For `search_nodes`, the summary currently includes:

- match count;
- top match node IDs;
- titles;
- labels;
- text previews;
- query assembly diagnostics, when available;
- up to five compact fallback search probes, when available.

For `expand_proximity`, the summary includes:

- match count;
- adjacent node IDs;
- titles;
- labels;
- text previews;
- proximity scores;
- compact edge relation type, weight, confidence, and reviewed-edge provenance when present.

For `expand_graph_paths`, the summary includes:

- match count;
- path target node IDs;
- titles;
- labels;
- text previews;
- path score;
- path depth;
- compact path edge relation type, weight, confidence, and reviewed-edge provenance when present.

For `semantic_candidates`, the summary includes:

- match count;
- candidate node IDs;
- titles;
- labels;
- text previews;
- shared labels;
- shared label count.

This gives the memory-agent enough information to decide whether another read-only tool call is useful without injecting the full final answer context into the planner history. Query assembly appears both in the static prompt guidance and, when tool results are summarized, in the per-result history so later iterations can distinguish the original query guidance from diagnostics produced by a planner-issued sub-query.

## Final Answer Packaging

After the memory-agent loop stops, Python calls `build_agentic_answer_envelope()`.

That function:

1. Prepares tool results with `prepare_tool_results_for_answer()`.
2. Applies any validated memory-agent `context_proposal` from the final planner decision.
3. Builds a structured `context_document` from the prepared results and proposal.
4. Renders the prepared results with `render_tool_results()`.
5. Builds the final prompt text.
6. Computes token and character budget estimates.
7. Computes context metadata and included node IDs.

For `search_nodes`, `expand_proximity`, `expand_graph_paths`, and `semantic_candidates`, `prepare_tool_results_for_answer()` reduces the raw tool output to:

- top match;
- up to two assembled contexts;
- match count.

`assemble_search_contexts()` deduplicates records across contexts and enforces a shared 4,000-character context budget.

When the memory-agent stops, it may include:

```json
{
  "context_proposal": {
    "selected_node_ids": ["..."],
    "rationale": "why these nodes should shape the final context",
    "organization": ["how to order the context"]
  }
}
```

Python treats this as a proposal, not authority. It normalizes the proposal, ignores invented or absent node IDs during prioritization, keeps the existing context budget, and moves matching context records earlier in the rendered answer context where possible.

If the memory-agent sends an invalid tool call, Python returns a structured failed tool result instead of only raising a terse error. These failed results include:

- `error`: what was wrong;
- `usage`: how to call the tool correctly;
- `repair_instruction`: guidance to revise the next call and not repeat the invalid call.

This is deliberately visible to the next memory-agent iteration so the LLM can recover from tool-interface confusion.

The structured `context_document` is stored in prompt `context_metadata` with:

- schema version and context kind;
- original user query;
- controller decision;
- validated memory-agent context proposal, when present;
- evidence summary with tool counts, match/context/record counts, included node IDs, and source documents;
- per-tool result records;
- normalized search/query-assembly diagnostics where available;
- top search/proximity match, match count, and assembled context records;
- capped document-tree navigation output.

The final LLM still receives rendered Markdown prompt text. The structured context document exists for persistence, diagnostics, future API clients, and the eventual full context schema.

## Final Prompt Ordering

The rendered final answer context deliberately puts evidence before diagnostics:

1. Search query or proximity source node and match count.
2. Top match title and node ID.
3. Compiled source context.
4. Search diagnostics.

Diagnostics include:

- lexical terms;
- exact phrases;
- named anchors;
- near-match terms;
- fallback searches with result counts.

This ordering keeps the answer model focused on source evidence while still exposing why retrieval selected the context it did.

## Final LLM Call

The final answer prompt is sent through the selected answer adapter.

For `ollama_cli`, Python:

- builds the `ollama run` command;
- sends `prompt_text` over stdin;
- captures stdout;
- cleans terminal control characters, spinner output, and wrapped-word artifacts;
- returns an answer payload.

For `ollama_http`, Python:

- posts JSON to Ollama `/api/generate`;
- includes model, prompt, optional format, and optional thinking controls;
- returns the response text as an answer payload.

The mock adapter summarizes rendered context deterministically for tests and smoke checks.

## Provenance And Used Nodes

`used_node_ids` are Python-controlled. They are not taken from model claims.

The final answer payload derives used nodes from `prompt["context_metadata"]["included"]`.

For search results, included nodes are collected from records that actually survived answer-context assembly. If no compiled records exist but a top match was visible in the prompt, Tirzah records that visible top match as a `search_match`.

This means `used_node_ids` means "nodes shown to the answer model", not "all nodes searched".

## Persistence

After the final adapter returns, Python saves the exchange with:

- user query;
- answer payload;
- prompt envelope;
- focus node;
- session ID;
- full process trace.

The returned API/CLI response includes:

- answer;
- adapter and model;
- used node IDs;
- budget metadata;
- retrieval status;
- process trace.

## Current Boundary

The implemented interface is a structured prompt/tool protocol:

1. LLM proposes read-only tool calls.
2. Python validates and executes those calls.
3. The memory-agent can propose a `controller_decision` when stopping; Python validates that proposal and enriches it with actual tool/context counts.
4. Python assembles source-grounded context and includes a compact Controller Decision section in the final answer prompt before tool results.
4. Final LLM answers from that context.
5. Python records provenance and trace data.

The LLM cannot directly execute tools, query MongoDB, mutate state, or decide provenance.
