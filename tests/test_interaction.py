from mnemosyne.config import AppConfig, RuntimeConfig
from mnemosyne.sessions.interaction import (
    answer_query,
    build_memory_agent_prompt,
    build_query_assembly,
    combined_query_text,
    execute_search_nodes_tool,
    fallback_queries,
    memory_agent_runtime_config,
    parse_memory_agent_decision,
    parse_tool_calls,
    prepare_tool_results_for_answer,
    render_tool_results,
    included_nodes_from_tool_results,
    score_node_match,
    select_focus_node,
)


class FakeDb:
    pass


def test_select_focus_node_returns_none_without_matches(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    monkeypatch.setattr(interaction, "search_nodes", lambda *args, **kwargs: [])

    assert select_focus_node(FakeDb(), "missing") is None


def test_select_focus_node_falls_back_to_ranked_terms(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    def fake_search_nodes(_db, query=None, label=None, limit=5):
        if query == "Mnemosyne" and label == "source_chunk":
            return [
                {
                    "node_id": "generic",
                    "title": "Worker Smoke",
                    "text_preview": "Mnemosyne ingestion worker",
                },
                {
                    "node_id": "technical",
                    "title": "Mnemosyne Technical Design Document",
                    "text_preview": "Architecture notes",
                },
            ]
        return []

    monkeypatch.setattr(interaction, "search_nodes", fake_search_nodes)

    assert select_focus_node(FakeDb(), "What does the Mnemosyne technical design say?") == "technical"


def test_answer_query_uses_prompt_without_focus_node(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    captured = {}

    class FakeAnswerAdapter:
        def answer(self, prompt):
            captured["prompt"] = prompt
            return {
                "adapter": "fake",
                "answer": "direct answer",
                "used_node_ids": [],
            }

    monkeypatch.setattr(interaction, "select_focus_node", lambda *args, **kwargs: None)
    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")

    result = answer_query(FakeDb(), AppConfig(), "plain prompt")

    assert result["ok"] is True
    assert result["focus_node_id"] is None
    assert result["retrieval_status"] == "no_focus_node"
    assert result["used_node_ids"] == []
    assert "plain prompt" in captured["prompt"]["context_text"]
    assert [step["step"] for step in result["process_trace"]] == [
        "user_prompt",
        "retrieval_context",
        "answer_adapter",
    ]
    assert "plain prompt" in result["process_trace"][1]["output"]["context_text"]


def test_parse_tool_calls_extracts_json() -> None:
    calls = parse_tool_calls(
        '```json\n{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory","limit":2}}]}\n```'
    )

    assert calls == [
        {
            "tool": "search_nodes",
            "arguments": {"query": "memory", "limit": 2},
        }
    ]


def test_parse_tool_calls_allows_model_newlines_inside_strings() -> None:
    calls = parse_tool_calls(
        '{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"technical\n design"}}]}'
    )

    assert calls[0]["arguments"]["query"] == "technical\n design"


def test_parse_tool_calls_skips_thinking_json_fragments() -> None:
    calls = parse_tool_calls(
        'Thinking about {"draft": true}.\n'
        '{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory"}}]}'
    )

    assert calls == [
        {
            "tool": "search_nodes",
            "arguments": {"query": "memory"},
        }
    ]


def test_parse_tool_calls_scans_invalid_whole_object_response() -> None:
    calls = parse_tool_calls(
        '{"draft": true}\n'
        '{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory"}}]}'
    )

    assert calls[0]["arguments"]["query"] == "memory"


def test_agentic_answer_query_runs_planner_tools_then_answer(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    prompts = []

    class FakeAnswerAdapter:
        def answer(self, prompt):
            prompts.append(prompt["prompt_text"])
            if len(prompts) == 1:
                return {
                    "adapter": "fake",
                    "answer": '{"status":"continue","tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory"}}]}',
                    "used_node_ids": [],
                }
            if len(prompts) == 2:
                return {
                    "adapter": "fake",
                    "answer": '{"status":"done","tool_calls":[],"compiled_context_notes":"enough"}',
                    "used_node_ids": [],
                }
            return {
                "adapter": "fake",
                "answer": "final answer",
                "used_node_ids": ["node1"],
            }

    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    monkeypatch.setattr(
        interaction,
        "execute_tool_calls",
        lambda _db, calls, original_query=None: [
            {
                "index": 0,
                "tool": calls[0]["tool"],
                "arguments": calls[0]["arguments"],
                "ok": True,
                "output": [{"node_id": "node1", "title": "Memory"}],
            }
        ],
    )
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")
    config = AppConfig(runtime=RuntimeConfig(retrieval_mode="agentic", answer_adapter="fake"))

    result = answer_query(FakeDb(), config, "find memory")

    assert result["ok"] is True
    assert result["answer"] == "final answer"
    assert [step["step"] for step in result["process_trace"]] == [
        "user_prompt",
        "memory_agent_iteration",
        "memory_agent_iteration",
        "answer_adapter",
    ]
    assert result["process_trace"][1]["output"]["tool_results"][0]["tool"] == "search_nodes"
    assert result["process_trace"][2]["output"]["stopped"] is True
    assert "Mnemosyne Tool Results" in prompts[2]


def test_agentic_answer_query_falls_back_when_planner_stops_without_tools(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    prompts = []
    executed = []

    class FakeAnswerAdapter:
        def answer(self, prompt):
            prompts.append(prompt["prompt_text"])
            if "You are the Mnemosyne memory-agent." in prompt["prompt_text"]:
                return {
                    "adapter": "fake",
                    "answer": '{"status":"done","tool_calls":[],"compiled_context_notes":"none"}',
                    "used_node_ids": [],
                }
            return {
                "adapter": "fake",
                "answer": "final answer",
                "used_node_ids": ["node1"],
            }

    def fake_execute_tool_calls(_db, calls, original_query=None):
        executed.extend(calls)
        return [
            {
                "index": 0,
                "tool": calls[0]["tool"],
                "arguments": calls[0]["arguments"],
                "ok": True,
                "output": {
                    "matches": [{"node_id": "node1", "title": "Memory"}],
                    "compiled_contexts": [{"focus_node_id": "node1", "records": []}],
                },
            }
        ]

    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    monkeypatch.setattr(interaction, "execute_tool_calls", fake_execute_tool_calls)
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")
    config = AppConfig(
        runtime=RuntimeConfig(
            retrieval_mode="agentic",
            answer_adapter="fake",
            memory_agent_ollama_format=None,
        )
    )

    result = answer_query(FakeDb(), config, "find memory")

    assert result["ok"] is True
    assert executed == [{"tool": "search_nodes", "arguments": {"query": "find memory", "limit": 5}}]
    assert result["process_trace"][1]["output"]["decision"]["fallback_reason"] == (
        "memory_agent_returned_no_initial_tool_calls"
    )
    assert "stop_reason" not in result["process_trace"][1]["output"]
    assert result["process_trace"][2]["output"]["stopped"] is True
    assert [step["step"] for step in result["process_trace"]] == [
        "user_prompt",
        "memory_agent_iteration",
        "memory_agent_iteration",
        "answer_adapter",
    ]
    assert result["answer"] == "final answer"
    assert "Mnemosyne Tool Results" in prompts[-1]


def test_agentic_answer_query_stops_after_parse_failure_fallback_context(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    prompts = []
    executed = []

    class FakeAnswerAdapter:
        def answer(self, prompt):
            prompts.append(prompt["prompt_text"])
            if "You are the Mnemosyne memory-agent." in prompt["prompt_text"]:
                return {
                    "adapter": "fake",
                    "answer": "not json",
                    "used_node_ids": [],
                }
            return {
                "adapter": "fake",
                "answer": "final answer",
                "used_node_ids": ["node1"],
            }

    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    def fake_execute_tool_calls(_db, calls, original_query=None):
        executed.extend(calls)
        return [
            {
                "index": 0,
                "tool": calls[0]["tool"],
                "arguments": calls[0]["arguments"],
                "ok": True,
                "output": {
                    "matches": [{"node_id": "node1", "title": "Memory"}],
                    "compiled_contexts": [{"focus_node_id": "node1", "records": []}],
                },
            }
        ]

    monkeypatch.setattr(interaction, "execute_tool_calls", fake_execute_tool_calls)
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")
    config = AppConfig(runtime=RuntimeConfig(retrieval_mode="agentic", answer_adapter="fake"))

    result = answer_query(FakeDb(), config, "find memory")

    assert result["ok"] is True
    assert result["answer"] == "final answer"
    assert executed == [{"tool": "search_nodes", "arguments": {"query": "find memory", "limit": 5}}]
    assert len(prompts) == 2
    assert [step["step"] for step in result["process_trace"]] == [
        "user_prompt",
        "memory_agent_iteration",
        "answer_adapter",
    ]
    assert result["process_trace"][1]["output"]["ok"] is True
    assert result["process_trace"][1]["output"]["decision"]["fallback_reason"] == (
        "memory_agent_decision_failed"
    )
    assert result["process_trace"][1]["output"]["stop_reason"] == "fallback_context_gathered"
    assert "Mnemosyne Tool Results" in prompts[-1]


def test_agentic_answer_query_marks_unavailable_fallback_context(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    class FakeAnswerAdapter:
        def answer(self, prompt):
            if "You are the Mnemosyne memory-agent." in prompt["prompt_text"]:
                return {
                    "adapter": "fake",
                    "answer": "not json",
                    "used_node_ids": [],
                }
            return {
                "adapter": "fake",
                "answer": "final answer",
                "used_node_ids": [],
            }

    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    monkeypatch.setattr(interaction, "execute_tool_calls", lambda *args, **kwargs: [])
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")
    config = AppConfig(runtime=RuntimeConfig(retrieval_mode="agentic", answer_adapter="fake"))

    result = answer_query(FakeDb(), config, "find memory")

    assert result["ok"] is True
    assert result["retrieval_status"] == "agentic_no_tool_context"
    assert result["process_trace"][1]["output"]["ok"] is False
    assert result["process_trace"][1]["output"]["stop_reason"] == "fallback_context_unavailable"


def test_agentic_answer_query_stops_when_planner_fails_after_context(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    prompts = []

    class FakeAnswerAdapter:
        def answer(self, prompt):
            prompts.append(prompt["prompt_text"])
            if len(prompts) == 1:
                return {
                    "adapter": "fake",
                    "answer": '{"status":"continue","tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory"}}]}',
                    "used_node_ids": [],
                }
            if "You are the Mnemosyne memory-agent." in prompt["prompt_text"]:
                return {
                    "adapter": "fake",
                    "answer": "not json",
                    "used_node_ids": [],
                }
            return {
                "adapter": "fake",
                "answer": "final answer",
                "used_node_ids": ["node1"],
            }

    monkeypatch.setattr(interaction, "answer_adapter", lambda _config: FakeAnswerAdapter())
    monkeypatch.setattr(
        interaction,
        "execute_tool_calls",
        lambda _db, calls, original_query=None: [
            {
                "index": 0,
                "tool": calls[0]["tool"],
                "arguments": calls[0]["arguments"],
                "ok": True,
                "output": {
                    "matches": [{"node_id": "node1", "title": "Memory"}],
                    "compiled_contexts": [{"focus_node_id": "node1", "records": []}],
                },
            }
        ],
    )
    monkeypatch.setattr(interaction, "save_exchange", lambda *args, **kwargs: "exchange1")
    config = AppConfig(runtime=RuntimeConfig(retrieval_mode="agentic", answer_adapter="fake"))

    result = answer_query(FakeDb(), config, "find memory")

    assert result["ok"] is True
    assert result["answer"] == "final answer"
    assert [step["step"] for step in result["process_trace"]] == [
        "user_prompt",
        "memory_agent_iteration",
        "memory_agent_iteration",
        "answer_adapter",
    ]
    assert result["process_trace"][2]["output"]["ok"] is False
    assert result["process_trace"][2]["output"]["decision"]["fallback_reason"] == (
        "memory_agent_failed_after_tool_context"
    )
    assert result["process_trace"][2]["output"]["stop_reason"] == (
        "memory_agent_failed_after_tool_context"
    )
    assert "Mnemosyne Tool Results" in prompts[-1]


def test_parse_memory_agent_decision_accepts_done_status() -> None:
    decision = parse_memory_agent_decision(
        '{"status":"done","tool_calls":[],"compiled_context_notes":"sufficient"}'
    )

    assert decision == {
        "status": "done",
        "tool_calls": [],
        "compiled_context_notes": "sufficient",
    }


def test_memory_agent_runtime_can_differ_from_answer_runtime() -> None:
    runtime = RuntimeConfig(
        answer_adapter="ollama_cli",
        ollama_model="final",
        memory_agent_adapter="ollama_http",
        memory_agent_model="memory",
        memory_agent_ollama_format="json",
    )

    memory_runtime = memory_agent_runtime_config(runtime)

    assert memory_runtime.answer_adapter == "ollama_http"
    assert memory_runtime.ollama_model == "memory"
    assert memory_runtime.ollama_format == "json"
    assert runtime.answer_adapter == "ollama_cli"
    assert runtime.ollama_model == "final"
    assert runtime.ollama_format is None


def test_build_memory_agent_prompt_includes_query_assembly_guidance() -> None:
    prompt = build_memory_agent_prompt(
        query="What does the Mnemosyne technical design say the system is for?",
        focus_node_id=None,
        history=[],
    )

    assert "Query assembly:" in prompt
    assert "- Lexical terms: Mnemosyne, technical, design, system" in prompt
    assert "- Exact phrases: Mnemosyne technical, technical design, design system" in prompt
    assert "- Named anchors: Mnemosyne" in prompt
    assert "- Suggested fallback searches: Mnemosyne technical" in prompt


def test_execute_search_nodes_tool_falls_back_to_terms(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    calls = []

    def fake_search_nodes(_db, query=None, label=None, limit=5):
        calls.append(query)
        if query == "Mnemosyne":
            return [{"node_id": "node1", "title": "Mnemosyne"}]
        return []

    monkeypatch.setattr(interaction, "search_nodes", fake_search_nodes)
    monkeypatch.setattr(interaction, "compile_context", lambda _db, _node_id: None)

    output, details = execute_search_nodes_tool(
        FakeDb(),
        query="technical desig\ndesign Mnemosyne",
    )

    assert output["matches"] == [{"node_id": "node1", "title": "Mnemosyne"}]
    assert output["compiled_contexts"] == []
    assert calls[0] == "technical desig design Mnemosyne"
    assert "desig design" in details["query_assembly"]["exact_phrases"]
    assert any(item["query"] == "Mnemosyne" for item in details["fallback_queries"])


def test_execute_search_nodes_tool_uses_original_query_for_intent_terms(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    def fake_search_nodes(_db, query=None, label=None, limit=5):
        if query == "system":
            return [
                {
                    "node_id": "generic",
                    "title": "Mnemosyne Technical Design Document",
                    "text_preview": "Header",
                    "labels": ["source_root"],
                },
                {
                    "node_id": "concept",
                    "title": "1. System Name and Concept",
                    "text_preview": "Mnemosyne is a locally operated memory layer.",
                    "labels": ["source_section"],
                },
            ]
        return []

    monkeypatch.setattr(interaction, "search_nodes", fake_search_nodes)
    monkeypatch.setattr(interaction, "compile_context", lambda _db, _node_id: None)

    output, details = execute_search_nodes_tool(
        FakeDb(),
        query="Mnemosyne technical design",
        original_query="What does the Mnemosyne technical design say the system is for?",
    )

    assert output["matches"][0]["node_id"] == "concept"
    assert details["ranking_query"] == (
        "Mnemosyne technical design What does the say system is for"
    )
    assert details["query_assembly"]["lexical_terms"] == [
        "Mnemosyne",
        "technical",
        "design",
        "system",
    ]
    assert any(item["query"] == "system" for item in details["fallback_queries"])


def test_execute_search_nodes_tool_compiles_top_match(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    monkeypatch.setattr(
        interaction,
        "search_nodes",
        lambda *args, **kwargs: [{"node_id": "node1", "title": "Mnemosyne"}],
    )
    monkeypatch.setattr(
        interaction,
        "compile_context",
        lambda _db, node_id: {"focus_node_id": node_id, "records": []},
    )

    output, _details = execute_search_nodes_tool(FakeDb(), query="Mnemosyne")

    assert output["compiled_contexts"] == [{"focus_node_id": "node1", "records": []}]


def test_score_node_match_prefers_specific_title_terms() -> None:
    technical = {
        "title": "Mnemosyne Technical Design Document",
        "text_preview": "Architecture notes",
    }
    generic = {
        "title": "Worker Smoke",
        "text_preview": "Mnemosyne ingestion worker",
    }

    assert score_node_match(technical, "Mnemosyne technical design") > score_node_match(
        generic,
        "Mnemosyne technical design",
    )


def test_score_node_match_prefers_intent_section_over_generic_header() -> None:
    generic = {
        "title": "Mnemosyne Technical Design Document",
        "text_preview": "Header",
        "labels": ["source_root"],
    }
    concept = {
        "title": "1. System Name and Concept",
        "text_preview": "Mnemosyne is a locally operated memory layer.",
        "labels": ["source_section"],
    }

    assert score_node_match(concept, "Mnemosyne technical design system") > score_node_match(
        generic,
        "Mnemosyne technical design system",
    )


def test_score_node_match_keeps_named_project_above_broad_corpus_hits() -> None:
    ams_system = {
        "title": "2. It strengthens whole-system reading",
        "text_preview": "Coherence helps explain system-level dependence.",
        "labels": ["source_section", "ams_domain"],
        "provenance": {"source_path": "/home/cello/domains/AMS/coherence.md"},
    }
    mnemosyne_concept = {
        "title": "1. System Name and Concept",
        "text_preview": "Mnemosyne is a locally operated memory layer.",
        "labels": ["source_section"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }

    assert score_node_match(
        mnemosyne_concept,
        "What does the Mnemosyne technical design say the system is for?",
    ) > score_node_match(
        ams_system,
        "What does the Mnemosyne technical design say the system is for?",
    )


def test_score_node_match_allows_partial_multi_anchor_matches() -> None:
    partial = {
        "title": "Mnemosyne Technical Design Document",
        "text_preview": "Architecture notes.",
        "labels": ["source_section"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }
    weak = {
        "title": "Technical notes",
        "text_preview": "Generic notes.",
        "labels": ["source_section"],
        "provenance": {"source_path": "notes.md"},
    }

    assert score_node_match(
        partial,
        "Compare AMS Mnemosyne Technical design notes",
    ) > score_node_match(
        weak,
        "Compare AMS Mnemosyne Technical design notes",
    )


def test_score_node_match_ignores_sentence_initial_stopword_anchor() -> None:
    row = {
        "title": "Mnemosyne Configuration",
        "text_preview": "Configuration notes.",
        "labels": ["source_section"],
    }

    assert score_node_match(row, "This Mnemosyne configuration") > 0


def test_score_node_match_ignores_command_verb_anchor() -> None:
    row = {
        "title": "Mnemosyne Configuration",
        "text_preview": "Configuration notes.",
        "labels": ["source_section"],
    }

    assert score_node_match(row, "Find Mnemosyne configuration") > 0


def test_score_node_match_demotes_document_root() -> None:
    root = {
        "title": "Mnemosyne Technical Design Document",
        "text_preview": "System Name and Concept",
        "labels": ["source_root"],
    }
    section = {
        "title": "1. System Name and Concept",
        "text_preview": "Mnemosyne is a memory layer.",
        "labels": ["source_section"],
    }

    assert score_node_match(section, "Mnemosyne technical design system") > score_node_match(
        root,
        "Mnemosyne technical design system",
    )


def test_score_node_match_demotes_document_metadata_section_below_concept() -> None:
    metadata_section = {
        "title": "Mnemosyne Technical Design Document",
        "text_preview": "**Version:** 0.1\n**Status:** For Review\n**Date:** May 2026",
        "labels": ["source_section"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }
    concept = {
        "title": "1. System Name and Concept",
        "text_preview": "Mnemosyne is a locally operated memory layer.",
        "labels": ["source_section"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }

    assert score_node_match(
        concept,
        "What does the Mnemosyne technical design say the system is for?",
    ) > score_node_match(
        metadata_section,
        "What does the Mnemosyne technical design say the system is for?",
    )


def test_score_node_match_demotes_separator_only_chunks() -> None:
    separator = {
        "title": "Mnemosyne Technical Design Document / paragraph 2",
        "text_preview": "---",
        "labels": ["source_chunk"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }
    concept = {
        "title": "1. System Name and Concept",
        "text_preview": "Mnemosyne is a locally operated memory layer.",
        "labels": ["source_section"],
        "provenance": {"source_path": "Mnemosyne_Technical_Design_v0.1.md"},
    }

    assert score_node_match(
        concept,
        "What does the Mnemosyne technical design say the system is for?",
    ) > score_node_match(
        separator,
        "What does the Mnemosyne technical design say the system is for?",
    )


def test_combined_query_text_deduplicates_planner_and_original_terms() -> None:
    assert combined_query_text("memory design", "What memory design does") == (
        "memory design What does"
    )


def test_build_query_assembly_extracts_terms_phrases_and_anchors() -> None:
    assembly = build_query_assembly(
        "technical design",
        "What does the Mnemosyne technical design say the system is for?",
    )

    assert assembly["ranking_query"] == (
        "technical design What does the Mnemosyne say system is for"
    )
    assert assembly["lexical_terms"] == ["technical", "design", "Mnemosyne", "system"]
    assert assembly["exact_phrases"][:3] == [
        "technical design",
        "Mnemosyne technical",
        "design system",
    ]
    assert assembly["anchor_terms"] == ["Mnemosyne"]


def test_fallback_queries_prefers_phrases_before_single_terms() -> None:
    assert fallback_queries("Mnemosyne technical design system")[:3] == [
        "Mnemosyne technical",
        "technical design",
        "design system",
    ]


def test_prepare_tool_results_for_answer_keeps_top_two_search_contexts() -> None:
    prepared = prepare_tool_results_for_answer(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "matches": [{"node_id": "top"}, {"node_id": "other"}],
                    "compiled_contexts": [
                        {
                            "focus_node_id": "top",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Top",
                                    "node_id": "top",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Top text.",
                                }
                            ],
                        },
                        {
                            "focus_node_id": "other",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Other",
                                    "node_id": "other",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Other text.",
                                }
                            ],
                        },
                    ],
                },
            }
        ]
    )

    assert prepared[0]["output"]["top_match"] == {"node_id": "top"}
    assert [context["focus_node_id"] for context in prepared[0]["output"]["top_contexts"]] == [
        "top",
        "other",
    ]
    assert prepared[0]["output"]["match_count"] == 2
    assert "matches" not in prepared[0]["output"]


def test_prepare_tool_results_for_answer_deduplicates_context_records() -> None:
    prepared = prepare_tool_results_for_answer(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "matches": [{"node_id": "top"}, {"node_id": "other"}],
                    "compiled_contexts": [
                        {
                            "focus_node_id": "top",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Top",
                                    "node_id": "shared",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Shared text.",
                                }
                            ],
                        },
                        {
                            "focus_node_id": "other",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Shared",
                                    "node_id": "shared",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Shared text.",
                                },
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Other",
                                    "node_id": "other",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Other text.",
                                },
                            ],
                        },
                    ],
                },
            }
        ]
    )

    contexts = prepared[0]["output"]["top_contexts"]
    assert [record["node_id"] for context in contexts for record in context["records"]] == [
        "shared",
        "other",
    ]


def test_prepare_tool_results_for_answer_skips_contexts_over_budget() -> None:
    prepared = prepare_tool_results_for_answer(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "matches": [{"node_id": "top"}],
                    "compiled_contexts": [
                        {
                            "focus_node_id": "top",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Oversized",
                                    "node_id": "large",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "x" * 5000,
                                }
                            ],
                        }
                    ],
                },
            }
        ]
    )

    assert prepared[0]["output"]["top_contexts"] == []


def test_render_tool_results_renders_context_as_markdown() -> None:
    rendered = render_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "arguments": {"query": "system"},
                "details": {
                    "query_assembly": {
                        "lexical_terms": ["system", "Mnemosyne"],
                        "exact_phrases": ["Mnemosyne system"],
                        "anchor_terms": ["Mnemosyne"],
                    },
                    "fallback_queries": [
                        {"query": "Mnemosyne system", "result_count": 2},
                        {"query": "system", "result_count": 5},
                    ],
                },
                "output": {
                    "match_count": 1,
                    "top_match": {"node_id": "node1", "title": "System Name"},
                    "top_contexts": [
                        {
                            "document": {"title": "Doc", "document_id": "doc1"},
                            "focus_node_id": "node1",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "System Name",
                                    "node_id": "node1",
                                    "labels": ["source_section"],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Mnemosyne is a memory layer.",
                                }
                            ],
                        },
                        {
                            "document": {"title": "Doc", "document_id": "doc1"},
                            "focus_node_id": "node2",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Purpose",
                                    "node_id": "node2",
                                    "labels": ["source_section"],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "It assembles context.",
                                }
                            ],
                        },
                    ],
                },
            }
        ]
    )

    assert "### search_nodes" in rendered
    assert "# Mnemosyne Context" in rendered
    assert "Mnemosyne is a memory layer." in rendered
    assert "It assembles context." in rendered
    assert "Compiled context 1:" in rendered
    assert "Compiled context 2:" in rendered
    assert "- Lexical terms: system, Mnemosyne" in rendered
    assert "- Exact phrases: Mnemosyne system" in rendered
    assert "- Named anchors: Mnemosyne" in rendered
    assert "- Fallback searches: Mnemosyne system (2), system (5)" in rendered
    assert "#### Mnemosyne Context" in rendered
    assert "\n# Mnemosyne Context" not in rendered
    assert "#### Compiled context" not in rendered
    assert '"top_contexts"' not in rendered


def test_render_tool_results_handles_empty_top_contexts() -> None:
    rendered = render_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "arguments": {"query": "missing"},
                "output": {
                    "match_count": 0,
                    "top_match": None,
                    "top_contexts": [],
                },
            }
        ]
    )

    assert "### search_nodes" in rendered
    assert "Compiled context" not in rendered


def test_included_nodes_from_tool_results_collects_multiple_contexts() -> None:
    included = included_nodes_from_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "top_match": {"node_id": "match-only"},
                    "top_contexts": [
                        {
                            "focus_node_id": "node1",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Node 1",
                                    "node_id": "node1",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "First rendered record.",
                                }
                            ],
                        },
                        {
                            "focus_node_id": "node2",
                            "records": [
                                {
                                    "role": "focus",
                                    "distance": 0,
                                    "title": "Node 2",
                                    "node_id": "node2",
                                    "labels": [],
                                    "endorsement_label": "unreviewed",
                                    "provenance": {},
                                    "text": "Second rendered record.",
                                }
                            ],
                        },
                    ]
                },
            }
        ]
    )

    assert {row["node_id"] for row in included} == {"node1", "node2"}
    assert all(row["chars"] < 500 for row in included)


def test_included_nodes_from_tool_results_falls_back_to_top_match_without_contexts() -> None:
    included = included_nodes_from_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "top_match": {"node_id": "match-only", "title": "Visible Search Hit"},
                    "top_contexts": [],
                },
            }
        ]
    )

    assert included == [
        {
            "node_id": "match-only",
            "role": "search_match",
            "distance": 0,
            "chars": len("- Top match: Visible Search Hit\n- Top node ID: match-only"),
        }
    ]


def test_included_nodes_from_tool_results_does_not_fall_back_when_context_records_duplicate() -> None:
    record = {
        "role": "focus",
        "distance": 0,
        "title": "Shared Node",
        "node_id": "shared-node",
        "labels": [],
        "endorsement_label": "unreviewed",
        "provenance": {},
        "text": "Shared rendered record.",
    }

    included = included_nodes_from_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "top_match": {"node_id": "shared-node", "title": "Shared Node"},
                    "top_contexts": [{"records": [record]}],
                },
            },
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "top_match": {"node_id": "second-match", "title": "Second Match"},
                    "top_contexts": [{"records": [record]}],
                },
            },
        ]
    )

    assert {row["node_id"] for row in included} == {"shared-node"}
    assert included[0]["role"] == "focus"


def test_included_nodes_from_tool_results_preserves_non_search_tool_nodes() -> None:
    included = included_nodes_from_tool_results(
        [
            {
                "tool": "compile_context",
                "ok": True,
                "output": {
                    "focus_node_id": "focus-node",
                    "records": [
                        {
                            "node_id": "record-node",
                            "role": "focus",
                            "distance": 0,
                            "text": "Compiled context text.",
                        }
                    ],
                },
            }
        ]
    )

    assert {row["node_id"] for row in included} == {"focus-node", "record-node"}


def test_included_nodes_from_tool_results_combines_search_and_non_search_nodes() -> None:
    included = included_nodes_from_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "top_match": {"node_id": "match-node", "title": "Search Match"},
                    "top_contexts": [],
                },
            },
            {
                "tool": "compile_context",
                "ok": True,
                "output": {
                    "focus_node_id": "focus-node",
                    "records": [{"node_id": "record-node", "role": "focus"}],
                },
            },
        ]
    )

    assert {row["node_id"] for row in included} == {
        "match-node",
        "focus-node",
        "record-node",
    }


def test_prepare_tool_results_for_answer_applies_aggregate_context_budget() -> None:
    record_text = "x" * 2600
    contexts = []
    for index in range(3):
        contexts.append(
            {
                "document": {"title": f"Doc {index}", "document_id": f"doc{index}"},
                "focus_node_id": f"node{index}",
                "records": [
                    {
                        "role": "focus",
                        "distance": 0,
                        "title": f"Node {index}",
                        "node_id": f"node{index}",
                        "labels": [],
                        "endorsement_label": "unreviewed",
                        "provenance": {},
                        "text": record_text,
                    }
                ],
            }
        )

    prepared = prepare_tool_results_for_answer(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "matches": [{"node_id": "node0"}, {"node_id": "node1"}, {"node_id": "node2"}],
                    "compiled_contexts": contexts,
                },
            }
        ]
    )

    assembled = prepared[0]["output"]["top_contexts"]
    rendered = render_tool_results(prepared)

    assert len(assembled) == 1
    assert assembled[0]["focus_node_id"] == "node0"
    assert len(rendered) < 5000
