import time

import pytest

from llm_client import classify_llm_error, retry_llm_call


class RateLimitError(Exception):
    status_code = 429


class AuthenticationError(Exception):
    status_code = 401


class InternalServerError(Exception):
    status_code = 500


class BadRequestError(Exception):
    status_code = 400


def test_classify():
    assert classify_llm_error(RateLimitError())[0] is True
    assert classify_llm_error(InternalServerError())[0] is True
    assert classify_llm_error(AuthenticationError())[0] is False
    assert classify_llm_error(BadRequestError())[0] is False
    assert classify_llm_error(ValueError("x"))[0] is False


def test_retry_then_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError()
        return "ok"

    assert retry_llm_call(flaky) == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential backoff


def test_auth_error_not_retried(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("should not sleep"))
    calls = {"n": 0}

    def denied():
        calls["n"] += 1
        raise AuthenticationError()

    with pytest.raises(AuthenticationError):
        retry_llm_call(denied)
    assert calls["n"] == 1


def test_exhausted_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def always_fail():
        raise InternalServerError()

    with pytest.raises(InternalServerError):
        retry_llm_call(always_fail, max_attempts=3)


def test_retry_after_header_respected(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    class Resp:
        headers = {"retry-after": "7"}

    class RL(RateLimitError):
        response = Resp()

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RL()
        return "ok"

    assert retry_llm_call(flaky) == "ok"
    assert sleeps == [7.0]
