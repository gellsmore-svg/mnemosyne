from mnemosyne.adapters.answer import MockAnswerAdapter, clean_ollama_output, summarize_context_text


def test_summarize_context_text_ignores_metadata_lines() -> None:
    summary = summarize_context_text(
        "# Mnemosyne Context\n"
        "Document: Doc\n"
        "Document ID: doc1\n"
        "## Context Records\n"
        "- Node ID: node1\n"
        "Actual content.\n"
    )

    assert summary == "Actual content."


def test_mock_answer_adapter_returns_used_nodes() -> None:
    answer = MockAnswerAdapter().answer(
        {
            "context_text": "Useful context.",
            "context_metadata": {
                "included": [{"node_id": "node1"}, {"node_id": "node2"}],
            },
        }
    )

    assert answer["adapter"] == "mock_answer"
    assert answer["used_node_ids"] == ["node1", "node2"]


def test_clean_ollama_output_strips_spinner_and_ansi() -> None:
    assert clean_ollama_output("\x1b[?25l⠙ \x1b[Kmnemosyne-ok\n") == "mnemosyne-ok"
