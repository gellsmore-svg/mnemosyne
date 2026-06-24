import json

from tirzah.config import AppConfig, RuntimeConfig
from tirzah.planning.recursive import (
    create_initial_plan,
    fallback_plan,
    process_frontend_request,
    revise_plan_recursively,
    revise_saved_plan,
    list_plan_revisions,
)


class Cursor(list):
    def sort(self, field, direction):
        super().sort(key=lambda row: row.get(field), reverse=direction < 0)
        return self
    def limit(self, value):
        return Cursor(self[:value])


class Collection:
    def __init__(self):
        self.rows = []
    def insert_one(self, row):
        self.rows.append(row)
    def find(self, query, projection=None):
        return Cursor([
            {key: value for key, value in row.items() if key != "_id"}
            for row in self.rows
            if all(row.get(key) == value for key, value in query.items())
        ])


class Db:
    def __init__(self):
        self.recursive_plans = Collection()


def payload(decision="revise", status="active", action="Gather evidence"):
    return json.dumps({
        "objective": "Complete the user request",
        "status": status,
        "steps": [
            {"id": "1", "construct": "STEP", "action": "Interpret the request", "status": "completed"},
            {"id": "2", "construct": "CALL", "action": action, "depends_on": ["1"], "allowed_tools": ["tirzah_retrieval"]},
            {"id": "3", "construct": "RECURSE", "action": "Revise from new information", "depends_on": ["2"]},
        ],
        "stopping_conditions": ["request complete", "revision limit reached"],
        "unresolved_questions": [],
        "revision_decision": decision,
        "revision_reason": "new evidence changes the next action",
    })


def test_initial_plan_is_versioned_cairn():
    plan = create_initial_plan("Research X", planner=lambda _prompt: payload())
    assert plan.revision == 1 and plan.parent_revision is None
    assert plan.plan_id.startswith("plan_")
    assert "PLAN " in plan.cairn_text
    assert "PROCESS FulfilRequest" in plan.cairn_text
    assert plan.steps[1].allowed_tools == ["tirzah_retrieval"]
    assert all(step.status == "pending" for step in plan.steps)


def test_malformed_planner_gets_bounded_fallback_plan():
    plan = create_initial_plan("Do the work", planner=lambda _prompt: "not json", max_steps=2)
    assert plan.revision_decision == "revise"
    assert len(plan.steps) == 2
    assert plan.steps[-1].construct == "CALL"
    assert "planner fallback" in plan.revision_reason


def test_recursive_revision_preserves_lineage_and_stops_when_stable():
    answers = iter([
        payload(decision="revise", action="Gather initial evidence"),
        payload(decision="stable", status="stable", action="Use the gathered evidence"),
        payload(decision="revise", action="should not run"),
    ])
    planner = lambda _prompt: next(answers)
    initial = create_initial_plan("Research X", planner=planner)
    revisions = revise_plan_recursively(
        initial,
        [{"fact": "evidence arrived"}, {"fact": "more"}],
        planner=planner,
        max_revisions=4,
    )
    assert len(revisions) == 2
    assert revisions[1].parent_revision == 1
    assert revisions[1].revision_decision == "stable"
    assert revisions[1].steps[1].action == "Use the gathered evidence"


def test_frontend_wrapper_executes_existing_pipeline_then_revises_and_persists():
    answers = iter([
        payload(decision="revise", action="Gather evidence"),
        payload(decision="stable", status="stable", action="Answer from evidence"),
    ])
    db = Db()
    calls = []
    def executor(_db, _config, **kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "answer": "result",
            "retrieval_status": "agentic_web_context",
            "used_node_ids": [],
            "process_trace": [{"step": "answer_adapter", "input": {}, "output": {"ok": True}}],
            "activity_report": {"context_construction": {"evidence_summary": {"web_source_count": 2}}},
            "activity_log": "Answer Activity",
        }
    result = process_frontend_request(
        db,
        AppConfig(runtime=RuntimeConfig(recursive_planning_enabled=True)),
        query="Research X",
        executor=executor,
        planner=lambda _prompt: next(answers),
        session_id="s1",
    )
    assert calls == [{"query": "Research X", "session_id": "s1"}]
    assert result["request_plan"]["revision"] == 2
    assert result["request_plan"]["status"] == "stable"
    assert len(result["plan_revisions"]) == 2
    assert [step["step"] for step in result["process_trace"]] == ["request_plan", "answer_adapter", "request_plan"]
    assert len(db.recursive_plans.rows) == 2
    assert db.recursive_plans.rows[0]["session_id"] == "s1"
    assert result["activity_log"].startswith("Request Plan")


def test_frontend_wrapper_can_be_disabled_per_request():
    result = process_frontend_request(
        Db(), AppConfig(), query="hello",
        executor=lambda _db, _config, **kwargs: {"ok": True, **kwargs},
        planning_enabled=False,
    )
    assert result == {"ok": True, "query": "hello"}


def test_saved_plan_can_be_revised_from_later_information():
    db = Db()
    initial = create_initial_plan("Research X", planner=lambda _prompt: payload())
    from tirzah.planning.recursive import save_plan_revision
    save_plan_revision(db, initial, session_id="s1")
    revised = revise_saved_plan(
        db,
        AppConfig(runtime=RuntimeConfig(planning_max_revisions=3)),
        plan_id=initial.plan_id,
        new_information={"fact": "later evidence"},
        planner=lambda _prompt: payload(decision="stable", status="stable", action="Use later evidence"),
        session_id="s1",
    )
    assert revised.revision == 2 and revised.parent_revision == 1
    assert revised.steps[1].action == "Use later evidence"
    assert [row["revision"] for row in list_plan_revisions(db, initial.plan_id)] == [1, 2]
