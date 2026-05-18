from mnemosyne.config import AppConfig, RuntimeConfig
from mnemosyne.sessions.interaction import (
    answer_query,
    combined_query_text,
    execute_search_nodes_tool,
    memory_agent_runtime_config,
    parse_memory_agent_decision,
    parse_tool_calls,
    prepare_tool_results_for_answer,
    render_tool_results,
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
    )

    memory_runtime = memory_agent_runtime_config(runtime)

    assert memory_runtime.answer_adapter == "ollama_http"
    assert memory_runtime.ollama_model == "memory"
    assert runtime.answer_adapter == "ollama_cli"
    assert runtime.ollama_model == "final"


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


def test_combined_query_text_deduplicates_planner_and_original_terms() -> None:
    assert combined_query_text("memory design", "What memory design does") == (
        "memory design What does"
    )


def test_prepare_tool_results_for_answer_keeps_only_top_search_context() -> None:
    prepared = prepare_tool_results_for_answer(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "output": {
                    "matches": [{"node_id": "top"}, {"node_id": "other"}],
                    "compiled_contexts": [{"focus_node_id": "top"}, {"focus_node_id": "other"}],
                },
            }
        ]
    )

    assert prepared[0]["output"]["top_match"] == {"node_id": "top"}
    assert prepared[0]["output"]["top_context"] == {"focus_node_id": "top"}
    assert prepared[0]["output"]["match_count"] == 2
    assert "matches" not in prepared[0]["output"]


def test_render_tool_results_renders_context_as_markdown() -> None:
    rendered = render_tool_results(
        [
            {
                "tool": "search_nodes",
                "ok": True,
                "arguments": {"query": "system"},
                "output": {
                    "match_count": 1,
                    "top_match": {"node_id": "node1", "title": "System Name"},
                    "top_context": {
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
                },
            }
        ]
    )

    assert "### search_nodes" in rendered
    assert "# Mnemosyne Context" in rendered
    assert "Mnemosyne is a memory layer." in rendered
    assert '"top_context"' not in rendered
