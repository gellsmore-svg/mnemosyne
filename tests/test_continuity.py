from bson import ObjectId

from tirzah.sessions.continuity import (
    persist_prompt_iteration,
    render_restart_markdown,
    render_session_continuity_text,
    session_continuity,
)


class InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class UpdateResult:
    def __init__(self, modified_count):
        self.modified_count = modified_count


class Cursor(list):
    def sort(self, field, direction=None):
        rows = list(self)
        sort_fields = field if isinstance(field, list) else [(field, direction)]
        for sort_field, sort_direction in reversed(sort_fields):
            rows.sort(key=lambda row: row.get(sort_field), reverse=sort_direction < 0)
        return Cursor(rows)

    def limit(self, limit):
        return Cursor(self[:limit])


class Collection:
    def __init__(self):
        self.rows = []

    def insert_one(self, row):
        inserted = dict(row)
        inserted["_id"] = inserted.get("_id") or ObjectId()
        self.rows.append(inserted)
        return InsertResult(inserted["_id"])

    def update_many(self, filter_query, update):
        modified = 0
        for row in self.rows:
            if not matches(row, filter_query):
                continue
            apply_update(row, update)
            modified += 1
        return UpdateResult(modified)

    def find(self, query=None):
        return Cursor([row for row in self.rows if matches(row, query or {})])

    def find_one(self, query=None, sort=None):
        rows = self.find(query or {})
        if sort:
            rows = rows.sort(sort)
        return rows[0] if rows else None


class Db:
    def __init__(self):
        self.session_continuity = Collection()


def test_persist_prompt_iteration_marks_latest_and_serializes_context() -> None:
    db = Db()
    first_exchange = str(ObjectId())
    second_exchange = str(ObjectId())
    focus_node_id = str(ObjectId())

    persist_prompt_iteration(
        db,
        exchange_id=first_exchange,
        session_id="s1",
        project_domain_id="project",
        conversation_domain_id="conversation",
        query="What first?",
        answer={"answer": "First answer", "adapter": "mock", "model": "mock"},
        prompt={"budget": {}, "context_metadata": {}},
        focus_node_id=None,
        used_node_ids=[],
        active_document_ids=[],
        process_trace=[],
    )
    persist_prompt_iteration(
        db,
        exchange_id=second_exchange,
        session_id="s1",
        project_domain_id="project",
        conversation_domain_id="conversation",
        query="What next?",
        answer={"answer": "Second answer", "adapter": "mock", "model": "mock"},
        prompt={
            "budget": {"estimated_prompt_tokens": 12},
            "context_metadata": {
                "controller_decision": {"action": "use_repository_context"},
                "evidence_summary": {"included_node_count": 1, "source_documents": []},
            },
        },
        focus_node_id=focus_node_id,
        used_node_ids=[focus_node_id],
        active_document_ids=["doc1"],
        process_trace=[{"step": "retrieval_context", "output": {"ok": True}}],
    )

    payload = session_continuity(db, session_id="s1")

    assert payload["latest"]["exchange_id"] == second_exchange
    assert payload["latest"]["query"] == "What next?"
    assert payload["latest"]["focus_node_id"] == focus_node_id
    assert payload["latest"]["used_node_ids"] == [focus_node_id]
    assert payload["latest"]["active_document_ids"] == ["doc1"]
    assert payload["latest"]["controller_decision"] == {"action": "use_repository_context"}
    assert payload["latest"]["process_trace_summary"] == [
        {"step": "retrieval_context", "ok": True, "reason": None, "error": None}
    ]
    assert len(payload["recent"]) == 2
    assert [row["is_latest"] for row in payload["recent"]] == [True, False]


def test_render_session_continuity_text_handles_empty_and_latest() -> None:
    assert "No prompt iteration" in render_session_continuity_text(
        {"session_id": "empty", "latest": None, "recent": []}
    )

    text = render_session_continuity_text(
        {
            "session_id": "s1",
            "latest": {
                "exchange_id": "ex1",
                "query": "Question?",
                "answer_preview": "Answer.",
                "focus_node_id": None,
                "used_node_ids": ["n1"],
                "active_document_ids": [],
                "controller_decision": {"action": "answer_without_repository_context"},
                "evidence_summary": {"included_node_count": 0, "source_documents": []},
            },
            "recent": [],
        }
    )

    assert "Latest exchange: ex1" in text
    assert "Controller decision: answer_without_repository_context" in text


def test_render_restart_markdown_empty_and_populated() -> None:
    empty = render_restart_markdown({"session_id": "s0", "latest": None, "recent": []})
    assert empty.startswith("# Restart State")
    assert "No prompt iteration has been saved" in empty

    markdown = render_restart_markdown(
        {
            "session_id": "s1",
            "latest": {
                "exchange_id": "ex1",
                "query": "Question?",
                "answer_preview": "Answer.",
                "focus_node_id": None,
                "used_node_ids": ["n1"],
                "active_document_ids": [],
                "answer_adapter": "mock",
                "answer_model": "mock",
                "created_at": "2026-06-16T00:00:00+00:00",
                "controller_decision": {"action": "use_repository_context", "reason": "match"},
                "evidence_summary": {"included_node_count": 1, "source_documents": ["d1"]},
                "process_trace_summary": [
                    {"step": "retrieval_context", "ok": True, "reason": None, "error": None}
                ],
            },
            "recent": [
                {"created_at": "2026-06-16T00:00:00+00:00", "query": "Question?", "is_latest": True},
                {"created_at": "2026-06-15T00:00:00+00:00", "query": "Earlier?", "is_latest": False},
            ],
        }
    )
    assert "## Latest exchange" in markdown
    assert "**Prompt:** Question?" in markdown
    assert "**Controller decision:** use_repository_context — match" in markdown
    assert "**Evidence:** 1 included node(s), 1 source document(s)" in markdown
    assert "`retrieval_context` — ok" in markdown
    assert "## Recent iterations" in markdown
    assert "*(latest)*" in markdown


def test_render_restart_markdown_from_persisted_iteration() -> None:
    db = Db()
    exchange_id = str(ObjectId())
    persist_prompt_iteration(
        db,
        exchange_id=exchange_id,
        session_id="s1",
        project_domain_id="project",
        conversation_domain_id="conversation",
        query="Resume from here?",
        answer={"answer": "Resumed answer", "adapter": "mock", "model": "mock"},
        prompt={"budget": {}, "context_metadata": {}},
        focus_node_id=None,
        used_node_ids=[],
        active_document_ids=[],
        process_trace=[],
    )
    markdown = render_restart_markdown(session_continuity(db, session_id="s1"))
    assert "Resume from here?" in markdown
    assert exchange_id in markdown


def matches(row, query):
    for key, expected in query.items():
        if row.get(key) != expected:
            return False
    return True


def apply_update(row, update):
    row.update(update.get("$set", {}))
    row.update(update.get("$setOnInsert", {}))
