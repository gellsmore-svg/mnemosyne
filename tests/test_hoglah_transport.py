"""Tirzah hoglah messaging-transport routing (broker-free).

Confirms that selecting hoglah_transport != "store" routes run_generate /
run_embedding through the messaging submitter (not the SQLite client), using a
fake submitter transport so no broker is required.
"""

from __future__ import annotations

import pytest

from tirzah.adapters.answer import HoglahAnswerAdapter
from tirzah.adapters.hoglah_runtime import HoglahJobRunner
from tirzah.config import RuntimeConfig


class _FakeTransport:
    def __init__(self) -> None:
        self.published: list[tuple[bytes, str]] = []
        self.kind_seen: list[str] = []

    def reply_destination(self):
        return "hoglah-results"

    def publish_request(self, body, *, correlation_id):
        self.published.append((body, correlation_id))

    def await_result(self, correlation_id, timeout):
        import json

        kind = json.loads(self.published[-1][0]).get("kind")
        self.kind_seen.append(kind)
        base = {"correlation_id": correlation_id, "status": "completed", "job_id": "jX"}
        if kind == "embed":
            return {**base, "embedding": [0.1, 0.2, 0.3], "embedding_dim": 3, "model": "m"}
        return {**base, "output": "queued answer", "model": "m"}

    def close(self):
        pass


@pytest.fixture
def fake_transport(monkeypatch):
    fake = _FakeTransport()
    monkeypatch.setattr(
        "hoglah.messaging_submitter.make_submitter_transport", lambda *a, **k: fake
    )
    return fake


def test_runner_routes_generate_through_messaging(fake_transport) -> None:
    cfg = RuntimeConfig(answer_adapter="hoglah", hoglah_transport="redis")
    runner = HoglahJobRunner(cfg)
    assert runner._client is None and runner._submitter is not None  # messaging, not store
    result = runner.run_generate("hello", model="m", tags=["tirzah"], metadata={})
    assert result["status"] == "completed"
    assert result["output"] == "queued answer"
    assert len(fake_transport.published) == 1
    assert fake_transport.kind_seen == ["generate"]


def test_runner_routes_embed_through_messaging(fake_transport) -> None:
    cfg = RuntimeConfig(embedding_adapter="hoglah", hoglah_transport="kafka")
    runner = HoglahJobRunner(cfg)
    result = runner.run_embedding("vorton", model="nomic-embed-text:latest")
    assert result["status"] == "completed"
    assert result["embedding"] == [0.1, 0.2, 0.3]
    assert fake_transport.kind_seen == ["embed"]


def test_answer_adapter_over_messaging(fake_transport) -> None:
    cfg = RuntimeConfig(answer_adapter="hoglah", hoglah_transport="rabbitmq", ollama_model="m")
    payload = HoglahAnswerAdapter(cfg).answer({"prompt_text": "what is a vorton?"})
    assert payload["answer"] == "queued answer"
    assert payload["hoglah_job_id"] == "jX"
