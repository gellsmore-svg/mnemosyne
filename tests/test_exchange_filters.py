from mnemosyne.sessions.exchanges import bounded_limit, exchange_filter_query


def test_exchange_filter_query_combines_session_runtime_and_text_filters() -> None:
    query = exchange_filter_query(
        session_id="design",
        query_text="memory.layer?",
        adapter="ollama_cli",
        model="qwen3.6:latest",
    )

    assert query["session_id"] == "design"
    assert query["answer.adapter"] == "ollama_cli"
    assert query["answer.model"] == "qwen3.6:latest"
    assert query["$or"][0]["query"].pattern == "memory\\.layer\\?"
    assert query["$or"][1]["answer.answer"].pattern == "memory\\.layer\\?"


def test_exchange_filter_limit_is_bounded() -> None:
    assert bounded_limit(0) == 1
    assert bounded_limit(500) == 100
