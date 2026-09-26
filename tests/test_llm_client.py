"""llm_client 空响应重试测试：deepseek 推理模型偶发返回空 content + 无 tool_calls，
下一轮会 400，故在 _call_openai_with_tools 里做显式重试。"""
import types

import pytest

import llm_client


class FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, msg):
        self.message = msg


class FakeUsage:
    prompt_tokens = 10
    completion_tokens = 20


class FakeResp:
    def __init__(self, msg):
        self.choices = [FakeChoice(msg)]
        self.usage = FakeUsage()


def _empty():
    return FakeResp(FakeMsg(None, None))


def _normal():
    return FakeResp(FakeMsg("ok", None))


@pytest.fixture
def _patch_openai(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: object())


def test_empty_then_success_retries(monkeypatch, _patch_openai):
    seq = [_empty(), _empty(), _normal()]
    monkeypatch.setattr(llm_client, "retry_llm_call",
                        lambda fn, max_attempts=5: seq.pop(0))
    r = llm_client._call_openai_with_tools([], "sys", "m", "url", "key", 100, [])
    assert r["stop_reason"] == "stop"
    assert r["content"] == [{"type": "text", "text": "ok"}]
    assert seq == []  # 3 次都被消费


def test_all_empty_raises(monkeypatch, _patch_openai):
    seq = [_empty(), _empty(), _empty()]
    monkeypatch.setattr(llm_client, "retry_llm_call",
                        lambda fn, max_attempts=5: seq.pop(0))
    with pytest.raises(RuntimeError, match="空响应"):
        llm_client._call_openai_with_tools([], "sys", "m", "url", "key", 100, [])


def test_tool_calls_present_no_retry(monkeypatch, _patch_openai):
    """有 tool_calls 时即使 content 空也不算空响应。"""
    tc = types.SimpleNamespace(id="id1", function=types.SimpleNamespace(
        name="tool_x", arguments='{"a": 1}'))
    seq = [FakeResp(FakeMsg(None, [tc]))]
    monkeypatch.setattr(llm_client, "retry_llm_call",
                        lambda fn, max_attempts=5: seq.pop(0))
    r = llm_client._call_openai_with_tools([], "sys", "m", "url", "key", 100, [])
    assert r["stop_reason"] == "tool_calls"
    assert r["content"][0]["type"] == "tool_use"
    assert r["content"][0]["name"] == "tool_x"
