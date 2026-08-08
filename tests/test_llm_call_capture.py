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
        "output": {
            "ok": True,
            "answer": "A vorton is…",
            "model": "gemma3:1b",
            "adapter": "ollama_http",
            "used_node_ids": ["n1", "n2"],
            # Galeed F2 / Tirzah instrumentation: usage + duration on the step.
            "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total": 52},
            "duration_ms": 180,
        },
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
    assert call["duration_ms"] == 180
    assert call["usage"]["prompt_tokens"] == 40
    assert call["usage"]["completion_tokens"] == 12
    assert call["tokens_in"] == 40
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


def test_memory_agent_iterations_become_llm_call_documents() -> None:
    db = FakeDb()
    tracer = Tracer(session_id="s", db=None, source="tirzah")
    record_llm_calls_from_trace(db, tracer, [
        {"step": "memory_agent_iteration",
         "input": {"iteration": 1, "model": "gemma3:1b", "adapter": "ollama_cli",
                   "prompt_text": "AGENT PROMPT round 1"},
         "output": {"raw_answer": '{"status": "continue", "tool_calls": []}'}},
        {"step": "memory_agent_iteration",
         "input": {"iteration": 2, "model": "gemma3:1b", "adapter": "ollama_cli",
                   "prompt_text": "AGENT PROMPT round 2"},
         "output": {"ok": False, "decision": {"error": "adapter timed out"}}},
        {"step": "answer_adapter",
         "input": {"model": "gemma3:1b", "prompt_text": "final prompt"},
         "output": {"ok": True, "answer": "final answer"}},
    ])
    calls = db["llm_calls"].rows
    assert [c["step_name"] for c in calls] == ["memory_agent_1", "memory_agent_2", "answer"]
    assert calls[0]["prompt"] == "AGENT PROMPT round 1"
    assert calls[0]["output"].startswith('{"status"')
    assert calls[0]["metadata"]["role"] == "memory_agent"
    assert calls[1]["status"] == "failed" and "timed out" in calls[1]["error"]
