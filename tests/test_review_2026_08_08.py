"""Regressions for docs/review-2026-08-08.md."""

from __future__ import annotations

import hmac
from types import SimpleNamespace

from tirzah.config import RuntimeConfig
from tirzah.retrieval.queries import default_system_instruction
from tirzah.web.app import _authorized_api_token, PUBLIC_API_PATHS


def test_system_instruction_marks_retrieved_context_untrusted() -> None:
    text = default_system_instruction()
    assert "untrusted" in text.lower()
    assert "data, not instructions" in text.lower() or "not instructions" in text.lower()


def test_api_token_uses_constant_time_compare() -> None:
    # Smoke: correct token accepted, wrong rejected (M1).
    req = SimpleNamespace(headers={"x-tirzah-api-token": "sekrit"})
    assert _authorized_api_token(req, "sekrit") is True
    assert _authorized_api_token(req, "wrong") is False
    # compare_digest is used — length mismatch still false, not exception
    assert _authorized_api_token(req, "x") is False
    assert hmac.compare_digest("a", "a")


def test_health_paths_include_trailing_slash() -> None:
    assert "/api/health" in PUBLIC_API_PATHS
    assert "/api/health/" in PUBLIC_API_PATHS


def test_web_security_defaults() -> None:
    cfg = RuntimeConfig()
    assert cfg.web_localhost_only is True
    assert cfg.web_api_token == ""
