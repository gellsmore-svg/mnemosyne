from tirzah.planning.execution_store import (
    compact_execution_summary,
    get_plan_execution,
    list_plan_executions,
    load_plan_execution,
    resume_steps_and_context,
    save_plan_execution,
)
from tirzah.planning.executor import build_default_handlers, interpret_plan
from tirzah.planning.recursive import CairnPlan, PlanStep


class Cursor(list):
    def sort(self, field, direction):
        super().sort(key=lambda row: row.get(field), reverse=direction < 0)
        return self

    def limit(self, value):
        return Cursor(self[:value])


class Collection:
    def __init__(self):
        self.rows = {}

    def update_one(self, query, update, upsert=False):
        key = (query["plan_id"], query["revision"], query["session_id"])
        existing = self.rows.get(key, {})
        merged = {**existing, **update.get("$set", {})}
        if upsert and "created_at" in update.get("$setOnInsert", {}):
            merged.setdefault("created_at", update["$setOnInsert"]["created_at"])
        self.rows[key] = merged

    def find_one(self, query):
        if "session_id" in query and "plan_id" not in query:
            matches = [
                row
                for row in self.rows.values()
                if all(row.get(key) == value for key, value in query.items())
            ]
            return matches[0] if matches else None
        key = (query["plan_id"], query["revision"], query["session_id"])
        row = self.rows.get(key)
        if row is None:
            return None
        if query.get("status") and row.get("status") != query["status"]:
            return None
        return row

    def find(self, query, projection=None):
        rows = [
            {key: value for key, value in row.items() if key != "_id"}
            for row in self.rows.values()
            if all(row.get(key) == value for key, value in query.items())
        ]
        return Cursor(rows)


class Db:
    def __init__(self):
        self.plan_executions = Collection()


def _two_step_plan():
    return CairnPlan(
        plan_id="plan_resume",
        revision=1,
        parent_revision=None,
        request="q",
        trigger="t",
        objective="q",
        status="active",
        steps=[
            PlanStep(id="1", action="r", construct="CALL", allowed_tools=["tirzah_retrieval"]),
            PlanStep(
                id="2",
                action="s",
                construct="CALL",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ],
    )


def test_list_and_get_plan_executions():
    db = Db()
    plan = _two_step_plan()
    save_plan_execution(
        db,
        plan=plan,
        session_id="s1",
        query="q",
        steps=plan.steps,
        completed_step_ids=[],
        artifacts={"retrieval_package": {"query": "q"}},
        trace=[],
        effects=[],
        status="running",
    )
    listed = list_plan_executions(db, "s1", status="running", limit=5)
    assert len(listed) == 1
    summary = compact_execution_summary(listed[0])
    assert summary["artifact_keys"] == ["retrieval_package"]
    assert "artifacts" not in summary
    assert get_plan_execution(db, plan.plan_id, plan.revision, "s1")["status"] == "running"
    assert load_plan_execution(db, plan.plan_id, plan.revision, "s1", status="completed") is None


def test_save_and_resume_execution_state():
    db = Db()
    plan = _two_step_plan()
    save_plan_execution(
        db,
        plan=plan,
        session_id="s1",
        query="q",
        steps=plan.steps,
        completed_step_ids=["1"],
        artifacts={"retrieval_package": {"query": "q", "session_id": "s1"}},
        trace=[{"step": "plan.step.completed", "step_id": "1"}],
        effects=["tirzah_retrieval"],
        status="running",
    )
    saved = load_plan_execution(db, plan.plan_id, plan.revision, "s1")
    assert saved is not None
    assert saved["completed_step_ids"] == ["1"]
    steps, completed, artifacts, trace, effects = resume_steps_and_context(saved)
    assert "1" in completed
    assert artifacts["retrieval_package"]["query"] == "q"
    assert "tirzah_retrieval" in effects


def test_interpret_plan_resumes_after_partial_execution(monkeypatch):
    db = Db()
    plan = _two_step_plan()
    save_plan_execution(
        db,
        plan=plan,
        session_id="s1",
        query="q",
        steps=[
            PlanStep(id="1", action="r", construct="CALL", status="completed", allowed_tools=["tirzah_retrieval"]),
            PlanStep(
                id="2",
                action="s",
                construct="CALL",
                status="pending",
                depends_on=["1"],
                allowed_tools=["answer_adapter"],
            ),
        ],
        completed_step_ids=["1"],
        artifacts={
            "retrieval_package": {
                "query": "q",
                "session_id": "s1",
                "focus_node_id": None,
                "selected_node_id": None,
                "retrieval_mode": "direct",
                "runtime_config": {"answer_adapter": "mock"},
                "process_trace": [],
                "prompt": {"prompt_text": "ctx", "budget": {}, "context_metadata": {}},
                "retrieval_status": "matched_context",
            }
        },
        trace=[],
        effects=["tirzah_retrieval"],
        status="running",
    )
    calls = {"retrieve": 0, "synthesize": 0}

    def retrieve(*_a, **_k):
        calls["retrieve"] += 1
        return {"ok": True, "package": {}}

    def synthesize(_db, _config, _package):
        calls["synthesize"] += 1
        return {"ok": True, "answer": "resumed", "used_node_ids": []}

    monkeypatch.setattr("tirzah.sessions.answer_phases.retrieve_for_answer", retrieve)
    monkeypatch.setattr("tirzah.sessions.answer_phases.synthesize_from_retrieval", synthesize)

    handlers = build_default_handlers(db=None, config=None, answer_kwargs={"session_id": "s1"})
    result = interpret_plan(
        plan,
        query="q",
        session_id="s1",
        handlers=handlers,
        db=db,
        persist_execution=True,
        resume_execution=True,
    )
    assert calls["retrieve"] == 0
    assert calls["synthesize"] == 1
    assert result.primary_result["answer"] == "resumed"
    assert result.plan.steps[0].status == "completed"
    assert result.plan.steps[1].status == "completed"