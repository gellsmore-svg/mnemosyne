from tirzah.sessions.answer_phases import AnswerRetrievalPackage, synthesize_from_retrieval


def test_synthesize_from_prebuilt_deep_package(monkeypatch):
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
        pre_built_answer={
            "answer": "deep answer",
            "used_node_ids": ["n1"],
            "adapter": "mock",
            "model": "m",
        },
    )

    result = synthesize_from_retrieval(None, None, package)
    assert result["ok"] is True
    assert result["answer"] == "deep answer"
    assert result["phase"] == "synthesis"
    assert saved["answer"]["answer"] == "deep answer"


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