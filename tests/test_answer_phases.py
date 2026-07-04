from tirzah.sessions.answer_phases import AnswerRetrievalPackage, synthesize_from_retrieval


def test_synthesize_from_deep_useful_chunks(monkeypatch):
    saved = {}

    def fake_save(db, **kwargs):
        saved.update(kwargs)
        return "ex-1"

    import tirzah.sessions.interaction as interaction

    monkeypatch.setattr(interaction, "save_exchange", fake_save)
    monkeypatch.setattr(interaction, "schedule_turn_embedding", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "schedule_chunking", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "finish_answer_process_run", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "attach_answer_activity", lambda result: result)
    monkeypatch.setattr(interaction, "render_session_history_block", lambda *a, **k: "")
    monkeypatch.setattr(
        "tirzah.retrieval.deep.synthesize_answer",
        lambda query, chunks, adapter, history_block="": "deep answer",
    )

    package = AnswerRetrievalPackage(
        query="q",
        session_id="s1",
        focus_node_id=None,
        selected_node_id=None,
        retrieval_mode="deep",
        runtime_config={"answer_adapter": "mock", "ollama_model": "m"},
        process_trace=[],
        process_run_id=None,
        prompt={"prompt_text": "", "budget": {}, "context_metadata": {"included": []}},
        retrieval_status="deep_context",
        useful_chunks=[{"node_id": "n1", "title": "T", "text": "body"}],
    )

    result = synthesize_from_retrieval(None, None, package)
    assert result["ok"] is True
    assert result["answer"] == "deep answer"
    assert result["phase"] == "synthesis"
    assert saved["answer"]["answer"] == "deep answer"
    adapter_steps = [row for row in result["process_trace"] if row.get("step") == "answer_adapter"]
    assert len(adapter_steps) == 1
    assert adapter_steps[0]["input"]["mode"] == "deep_synthesis"
    assert adapter_steps[0]["output"]["ok"] is True


def test_retrieve_deep_stores_chunks_not_prebuilt_answer(monkeypatch):
    import tirzah.sessions.answer_phases as phases
    import tirzah.sessions.interaction as interaction

    monkeypatch.setattr(interaction, "first_active_agent_identity", lambda db: None)
    monkeypatch.setattr(interaction, "answer_adapter", lambda rc: object())
    monkeypatch.setattr(interaction, "build_query_embedding", lambda rc, text: None)
    monkeypatch.setattr(
        "tirzah.retrieval.deep.run_deep_retrieval",
        lambda *a, **k: {
            "useful_chunks": [{"node_id": "n1", "title": "T", "text": "x"}],
            "rounds": [],
            "trace": [{"step": "stop", "reason": "planner_stop"}],
        },
    )
    from tirzah.config import AppConfig, RuntimeConfig

    runtime = RuntimeConfig(answer_adapter="mock")
    config = AppConfig()
    monkeypatch.setattr(phases, "_begin_answer_request", lambda *a, **k: (runtime, [], None))

    package = phases.AnswerRetrievalPackage(
        query="q",
        session_id="s1",
        focus_node_id=None,
        selected_node_id=None,
        retrieval_mode="deep",
        runtime_config={"answer_adapter": "mock"},
        process_trace=[],
    )
    result = phases._retrieve_deep(None, config, runtime, package)
    assert result["ok"] is True
    assert package.pre_built_answer is None
    assert package.useful_chunks == [{"node_id": "n1", "title": "T", "text": "x"}]


def test_split_handlers_invoke_both_phases(monkeypatch):
    from tirzah.planning.executor import build_default_handlers, interpret_plan
    from tirzah.planning.recursive import CairnPlan, PlanStep

    calls = []

    def retrieve(db, config, **kwargs):
        calls.append("retrieve")
        return {
            "ok": True,
            "package": {
                "query": kwargs["query"],
                "session_id": kwargs.get("session_id", "default"),
                "focus_node_id": None,
                "selected_node_id": None,
                "retrieval_mode": "direct",
                "runtime_config": {"answer_adapter": "mock"},
                "process_trace": [],
                "prompt": {"prompt_text": "ctx", "budget": {}, "context_metadata": {}},
                "retrieval_status": "matched_context",
            },
        }

    def synthesize(db, config, package):
        calls.append("synthesize")
        return {"ok": True, "answer": "final", "used_node_ids": []}

    monkeypatch.setattr("tirzah.sessions.answer_phases.retrieve_for_answer", retrieve)
    monkeypatch.setattr("tirzah.sessions.answer_phases.synthesize_from_retrieval", synthesize)

    plan = CairnPlan(
        plan_id="p",
        revision=1,
        parent_revision=None,
        request="q",
        trigger="t",
        objective="q",
        status="active",
        steps=[
            PlanStep(id="1", action="r", construct="CALL", allowed_tools=["tirzah_retrieval"]),
            PlanStep(id="2", action="s", construct="CALL", depends_on=["1"], allowed_tools=["answer_adapter"]),
        ],
    )
    handlers = build_default_handlers(db=None, config=None, answer_kwargs={"session_id": "s1"})
    result = interpret_plan(plan, query="q", session_id="s1", handlers=handlers)
    assert calls == ["retrieve", "synthesize"]
    assert result.primary_result["answer"] == "final"