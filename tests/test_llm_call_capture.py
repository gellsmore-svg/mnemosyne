"""Answer-path LLM call capture (sessions.process_events → galeed llm_calls)."""

from __future__ import annotations

from galeed.recorder import Tracer

from tirzah.sessions.process_events import record_llm_calls_from_trace


class FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_one(self, row):
        self.rows.append(dict(row))


class FakeDb:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())


def _trace(answer_step: dict) -> list[dict]:
    return [
        {"step": "user_prompt", "input": {"query": "q"}, "output": {}},
        {"step": "retrieval_context", "input": {}, "output": {"ok": True}},
        answer_step,
    ]


def test_answer_adapter_step_becomes_full_io_document() -> None:
    db = FakeDb()
    tracer = Tracer(session_id="sess-1", db=None, source="tirzah")
    record_llm_calls_from_trace(db, tracer, _trace({
        "step": "answer_adapter",
        "input": {"adapter": "ollama_http", "model": "gemma3:1b",
                  "prompt_text": "CONTEXT…\n\nQUESTION: what is a vorton?"},
        "output": {"ok": True, "answer": "A vorton is…", "model": "gemma3:1b",
                   "adapter": "ollama_http", "used_node_ids": ["n1", "n2"]},
    }))

    calls = db["llm_calls"].rows
    assert len(calls) == 1
    call = calls[0]
    assert call["trace_id"] == tracer.trace_id
    assert call["session_id"] == "sess-1"
    assert call["source"] == "tirzah"
    assert call["step_name"] == "answer"
    assert call["prompt"].startswith("CONTEXT…")   # the FULL prompt, not a summary
    assert call["output"] == "A vorton is…"
    assert call["status"] == "completed"
    assert call["metadata"]["used_node_count"] == 2
    # No extra spine event: model.response.completed already marks it.
    assert db["trace_events"].rows == []


def test_failed_adapter_step_records_error() -> None:
    db = FakeDb()
    tracer = Tracer(session_id="s", db=None, source="tirzah")
    record_llm_calls_from_trace(db, tracer, _trace({
        "step": "answer_adapter",
        "input": {"adapter": "ollama_http", "model": "gemma3:1b", "prompt_text": "p"},
        "output": {"ok": False, "error": "connection refused"},
    }))
    call = db["llm_calls"].rows[0]
    assert call["status"] == "failed"
    assert call["error"] == "connection refused"
    assert call["output"] is None


def test_non_adapter_steps_and_broken_db_are_harmless() -> None:
    tracer = Tracer(session_id="s", db=None, source="tirzah")
    db = FakeDb()
    record_llm_calls_from_trace(db, tracer, _trace({"step": "save_exchange", "input": {}, "output": {}}))
    assert db["llm_calls"].rows == []

    class BrokenDb:
        def __getitem__(self, name):
            raise RuntimeError("db down")

    # must not raise into the request path
    record_llm_calls_from_trace(BrokenDb(), tracer, _trace({
        "step": "answer_adapter", "input": {"prompt_text": "p"}, "output": {"ok": True, "answer": "a"},
    }))
