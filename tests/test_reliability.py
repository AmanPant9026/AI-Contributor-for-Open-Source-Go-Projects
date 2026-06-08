"""Reliability tests: the LLM transient-retry/backoff logic, exercised with injected
failures and a fake sleep (no network, no real waiting)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from go_issue_agent.llm.client import _is_transient, _retry_transient


def test_is_transient_classification():
    # transient: rate limits, 5xx, timeouts, connection issues
    assert _is_transient(Exception("Rate limit exceeded (429)"))
    assert _is_transient(Exception("Request timed out after 60s"))
    assert _is_transient(Exception("503 Service Unavailable"))
    assert _is_transient(Exception("Overloaded, please try again"))
    assert _is_transient(Exception("Connection reset by peer"))
    # NOT transient: these must fail fast, not retry
    assert not _is_transient(Exception("invalid x-api-key"))
    assert not _is_transient(Exception("400 Bad Request: malformed messages"))
    # a temperature rejection is handled by _completion_once, not the transient loop
    assert not _is_transient(Exception("temperature is not supported for this model"))


def test_retry_recovers_after_transient_failures():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("rate limit (429)")
        return "ok"

    slept: list[float] = []
    out = _retry_transient(fn, _sleep=slept.append)
    assert out == "ok"
    assert calls["n"] == 3            # failed twice, succeeded on the third
    assert slept == [1.0, 2.0]        # exponential backoff before retries 2 and 3


def test_retry_raises_non_transient_immediately():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        _retry_transient(fn, _sleep=lambda s: None)
    assert calls["n"] == 1            # no retries on a non-transient error


def test_retry_gives_up_after_max_then_raises():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise Exception("server overloaded 503")

    slept: list[float] = []
    with pytest.raises(Exception):
        _retry_transient(fn, max_retries=4, _sleep=slept.append)
    assert calls["n"] == 5            # initial attempt + 4 retries
    assert slept == [1.0, 2.0, 4.0, 8.0]
