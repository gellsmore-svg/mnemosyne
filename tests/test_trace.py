from __future__ import annotations

import queue

from tirzah.trace import (
    EventType,
    TraceBus,
    Tracer,
    list_trace_events,
)
from tirzah.trace.bus import WILDCARD


class FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_one(self, row):
        self.rows.append(dict(row))

    def find(self, query=None, projection=None):
        query = query or {}
        result = [
            {k: v for k, v in row.items() if k != "_id"}
            for row in self.rows
            if all(row.get(k) == val for k, val in query.items())
        ]
        return result


class FakeDb:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())


def test_emit_builds_structured_event_with_stable_ids_and_seq():
    bus = TraceBus()
    tracer = Tracer(trace_id="trace_x", session_id="sess_1", db=None, bus=bus, message_id="msg_1")
    ev1 = tracer.emit(EventType.PROCESS_STARTED, summary="started")
    ev2 = tracer.completed(EventType.ANSWER_FINALIZED, summary="done", answer="hi")

    assert ev1.trace_id == "trace_x" and ev1.session_id == "sess_1"
    assert ev1.source == "tirzah"
    assert ev1.message_id == "msg_1"
    assert ev1.seq == 1 and ev2.seq == 2  # monotonic per tracer
    assert ev2.status == "completed" and ev2.metadata == {"answer": "hi"}
    assert ev1.event_id != ev2.event_id
    assert len(tracer.events) == 2


def test_to_dict_is_json_safe():
    tracer = Tracer(session_id="s", db=None)
    ev = tracer.emit(EventType.MODEL_PROMPT_BUILT, summary="built", tokens=12)
    data = ev.to_dict()
    assert isinstance(data["timestamp"], str)  # ISO string, not datetime
    assert data["type"] == "model.prompt.built"
    assert data["metadata"]["tokens"] == 12


def test_unknown_event_type_is_allowed_extensible():
    tracer = Tracer(session_id="s", db=None)
    ev = tracer.emit("cairn.step.evaluated", status="ok", summary="future event")
    assert ev.type == "cairn.step.evaluated"  # not restricted to the documented set


def test_events_persist_and_replay_in_order():
    db = FakeDb()
    tracer = Tracer(trace_id="t1", session_id="s1", db=db)
    tracer.emit(EventType.RETRIEVAL_MONGO_STARTED, summary="searching")
    tracer.emit(EventType.RETRIEVAL_MONGO_COMPLETED, summary="done")

    rows = list_trace_events(db, trace_id="t1")
    assert [r["type"] for r in rows] == ["retrieval.mongo.started", "retrieval.mongo.completed"]
    assert all("_id" not in r for r in rows)
    # session filter also works
    assert len(list_trace_events(db, session_id="s1")) == 2
    assert list_trace_events(db, trace_id="other") == []


def test_emit_never_raises_on_broken_db():
    class BrokenDb:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    tracer = Tracer(session_id="s", db=BrokenDb())
    ev = tracer.emit(EventType.PROCESS_STARTED)  # must not raise
    assert ev.type == "process.started"
    assert len(tracer.events) == 1  # in-memory copy still kept


def test_bus_delivers_to_matching_channels_only():
    bus = TraceBus()
    with bus.subscribe("trace_a") as q_trace, bus.subscribe("sess_a") as q_sess, bus.subscribe(WILDCARD) as q_all:
        tracer = Tracer(trace_id="trace_a", session_id="sess_a", db=None, bus=bus)
        tracer.emit(EventType.PROCESS_STARTED, summary="go")

        got_trace = q_trace.get_nowait()
        got_sess = q_sess.get_nowait()
        got_all = q_all.get_nowait()
        assert got_trace.trace_id == got_sess.trace_id == got_all.trace_id == "trace_a"
        # each subscriber gets the event exactly once even though it matched 2 channels
        assert q_trace.empty() and q_sess.empty() and q_all.empty()

    # a non-matching subscriber receives nothing
    bus2 = TraceBus()
    with bus2.subscribe("other_trace") as q_other:
        Tracer(trace_id="trace_a", session_id="sess_a", db=None, bus=bus2).emit(EventType.PROCESS_STARTED)
        try:
            q_other.get_nowait()
            raise AssertionError("non-matching subscriber should receive nothing")
        except queue.Empty:
            pass


def test_subscribe_unsubscribes_on_exit():
    bus = TraceBus()
    with bus.subscribe("trace_z"):
        assert bus.subscriber_count() == 1
    assert bus.subscriber_count() == 0


def test_list_trace_sessions_summarises():
    from tirzah.trace import EventType, Tracer, list_trace_sessions

    db = FakeDb()
    t1 = Tracer(trace_id="trace_a", session_id="s1", db=db, source="tirzah")
    t1.emit(EventType.MESSAGE_USER_SUBMITTED, query="what is x?")
    t1.completed(EventType.ANSWER_FINALIZED, answer="x is a thing")
    t2 = Tracer(trace_id="trace_b", session_id="s1", db=db, source="tirzah-cli")
    t2.emit(EventType.MESSAGE_USER_SUBMITTED, query="follow up")
    Tracer(trace_id="trace_c", session_id="s2", db=db).emit(EventType.PROCESS_STARTED)

    sessions = list_trace_sessions(db)
    by_id = {s["session_id"]: s for s in sessions}
    assert set(by_id) == {"s1", "s2"}
    assert by_id["s1"]["trace_count"] == 2  # two requests in the session
    assert by_id["s1"]["event_count"] == 3
    assert sorted(by_id["s1"]["sources"]) == ["tirzah", "tirzah-cli"]
    assert by_id["s1"]["first_query"] == "what is x?"
    assert by_id["s1"]["last_answer_preview"] == "x is a thing"


def test_record_and_list_feedback():
    from tirzah.trace import list_feedback, record_feedback

    db = FakeDb()
    record = record_feedback(db, text="bug here", session_id="s1", trace_id="t1", kind="bug", source="claude")
    assert record["feedback_id"].startswith("fb_")
    assert record["status"] == "open" and record["kind"] == "bug" and record["source"] == "claude"

    rows = list_feedback(db, session_id="s1")
    assert len(rows) == 1 and rows[0]["text"] == "bug here"
    assert list_feedback(db, trace_id="other") == []


def test_record_feedback_never_raises_on_broken_db():
    from tirzah.trace import record_feedback

    class BrokenDb:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    record = record_feedback(BrokenDb(), text="x", session_id="s")  # must not raise
    assert record["feedback_id"].startswith("fb_")  # record still returned
