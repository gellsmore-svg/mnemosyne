from mnemosyne.config import AppConfig, RuntimeConfig
from mnemosyne.sessions.interaction import (
    answer_query,
    execute_search_nodes_tool,
    parse_tool_calls,
    select_focus_node,
)


class FakeDb:
    pass


def test_select_focus_node_returns_none_without_matches(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    monkeypatch.setattr(interaction, "search_nodes", lambda *args, **kwargs: [])

    assert select_focus_node(FakeDb(), "missing") is None


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
                    "answer": '{"tool_calls":[{"tool":"search_nodes","arguments":{"query":"memory"}}]}',
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
        lambda _db, calls: [
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
        "planner_adapter",
        "tool_execution",
        "answer_adapter",
    ]
    assert result["process_trace"][2]["output"]["tool_results"][0]["tool"] == "search_nodes"
    assert "Mnemosyne Tool Results" in prompts[1]


def test_execute_search_nodes_tool_falls_back_to_terms(monkeypatch) -> None:
    import mnemosyne.sessions.interaction as interaction

    calls = []

    def fake_search_nodes(_db, query=None, label=None, limit=5):
        calls.append(query)
        if query == "Mnemosyne":
            return [{"node_id": "node1", "title": "Mnemosyne"}]
        return []

    monkeypatch.setattr(interaction, "search_nodes", fake_search_nodes)

    results, details = execute_search_nodes_tool(
        FakeDb(),
        query="technical desig\ndesign Mnemosyne",
    )

    assert results == [{"node_id": "node1", "title": "Mnemosyne"}]
    assert calls[0] == "technical desig design Mnemosyne"
    assert any(item["query"] == "Mnemosyne" for item in details["fallback_queries"])
