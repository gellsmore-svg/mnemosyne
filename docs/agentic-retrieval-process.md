# Agentic Retrieval Process

Last updated: 2026-05-21

This document describes the implemented agentic retrieval and answer path in Mnemosyne. The key boundary is that Python owns retrieval, tool execution, prompt assembly, provenance, and persistence. LLMs only plan bounded read-only tool calls and produce the final answer from the context Python assembles.

## Runtime Roles

Mnemosyne currently separates two LLM-facing roles:

- Memory-agent LLM: plans retrieval. It must not answer the user. It returns JSON tool calls or a done decision.
- Final answer LLM: answers the user from the Mnemosyne tool results and rendered source context.

Both roles are called through the adapter boundary in `mnemosyne.adapters.answer`, so the concrete backend can be `mock`, `ollama_cli`, or `ollama_http`.

## Entry Point

All ask flows enter `answer_query()`.

Python records an initial `process_trace` item:

- step: `user_prompt`
- input: query, optional focus node, session, requested adapter/model, retrieval mode
- output: submitted prompt text

Runtime overrides are then applied. If `retrieval_mode` is `agentic`, Python calls `answer_query_agentic()`. Otherwise it uses the direct focus-node retrieval path.

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
What does the Mnemosyne technical design say the system is for?
```

Python derives:

- lexical terms: `Mnemosyne`, `technical`, `design`, `system`
- exact phrases: `Mnemosyne technical`, `technical design`, `design system`
- named anchors: `Mnemosyne`
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
- `list_documents`

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

This gives the memory-agent enough information to decide whether another read-only tool call is useful without injecting the full final answer context into the planner history. Query assembly appears both in the static prompt guidance and, when tool results are summarized, in the per-result history so later iterations can distinguish the original query guidance from diagnostics produced by a planner-issued sub-query.

## Final Answer Packaging

After the memory-agent loop stops, Python calls `build_agentic_answer_envelope()`.

That function:

1. Prepares tool results with `prepare_tool_results_for_answer()`.
2. Renders the prepared results with `render_tool_results()`.
3. Builds the final prompt text.
4. Computes token and character budget estimates.
5. Computes context metadata and included node IDs.

For `search_nodes`, `prepare_tool_results_for_answer()` reduces the raw tool output to:

- top match;
- up to two assembled contexts;
- match count.

`assemble_search_contexts()` deduplicates records across contexts and enforces a shared 4,000-character context budget.

## Final Prompt Ordering

The rendered final answer context deliberately puts evidence before diagnostics:

1. Search query and match count.
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

For search results, included nodes are collected from records that actually survived answer-context assembly. If no compiled records exist but a top match was visible in the prompt, Mnemosyne records that visible top match as a `search_match`.

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
3. Python assembles source-grounded context.
4. Final LLM answers from that context.
5. Python records provenance and trace data.

The LLM cannot directly execute tools, query MongoDB, mutate state, or decide provenance.
