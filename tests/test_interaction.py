from mnemosyne.config import AppConfig
from mnemosyne.sessions.interaction import answer_query, select_focus_node


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
