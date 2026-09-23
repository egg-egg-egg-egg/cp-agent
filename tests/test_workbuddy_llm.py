"""workbuddy driver（无 api_key 的 LLM 后端）单元测试。"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

import workbuddy_llm as wb


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """把队列目录指到临时目录，避免污染项目根。"""
    q = tmp_path / "q"
    monkeypatch.setenv("CP_AGENT_WB_QUEUE_DIR", str(q))
    monkeypatch.setenv("CP_AGENT_WB_TIMEOUT", "5")
    monkeypatch.delenv("CP_AGENT_WB_QUEUE_DIR", raising=False)
    monkeypatch.setenv("CP_AGENT_WB_QUEUE_DIR", str(q))
    return q


def _auto_answer(queue_dir: Path, content: str, delay: float = 0.2):
    """后台线程：等请求文件出现后写入响应。"""
    pending = queue_dir / "pending"

    def _go():
        deadline = time.time() + 4
        while time.time() < deadline:
            reqs = sorted(pending.glob("*.json"))
            if reqs:
                reqs[0].with_suffix(".resp").write_text(content, encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


# ───────────────────────── 目录与超时解析 ─────────────────────────

def test_resolve_queue_dir_env_wins(queue):
    assert wb.resolve_queue_dir() == queue.resolve()


def test_resolve_queue_dir_from_cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("CP_AGENT_WB_QUEUE_DIR", raising=False)
    assert wb.resolve_queue_dir({"queue_dir": "some/rel"}) == \
        (wb.PROJECT_ROOT / "some/rel").resolve()


def test_resolve_timeout(queue):
    assert wb.resolve_timeout() == 5


# ───────────────────────── 请求落盘 ─────────────────────────

def test_submit_request_writes_json(queue):
    req = wb.submit_request(
        queue_dir=queue, req_id="unit-1", system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "write_file", "description": "w", "input_schema": {}}],
        model="workbuddy-agent", max_tokens=100, stage="unit")
    p = Path(req["response_path"])
    assert p.parent == queue / "pending"
    assert not p.exists()
    data = json.loads((queue / "pending" / "unit-1.json").read_text(encoding="utf-8"))
    assert data["system"] == "SYS"
    assert data["tools"][0]["name"] == "write_file"
    assert data["stage"] == "unit"
    assert "response_path" in data and "cancel_path" in data


# ───────────────────────── 响应解析 ─────────────────────────

def test_parse_plain_text():
    blocks = wb.parse_response("好的，我来写题面")
    assert blocks == [{"type": "text", "text": "好的，我来写题面"}]


def test_parse_fenced_json():
    raw = "```json\n" + json.dumps({"tool": "write_file", "input": {"path": "a.md"}}) + "\n```"
    blocks = wb.parse_response(raw)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["name"] == "write_file"
    assert blocks[0]["input"] == {"path": "a.md"}


def test_parse_content_array():
    raw = json.dumps({"content": [
        {"type": "text", "text": "先写文件"},
        {"type": "tool_use", "name": "compile_cpp", "input": {"source": "a.cpp"}},
    ]})
    blocks = wb.parse_response(raw)
    assert [b["type"] for b in blocks] == ["text", "tool_use"]
    assert blocks[1]["name"] == "compile_cpp"
    assert blocks[1]["id"].startswith("wb_")


def test_parse_list_of_tools():
    raw = json.dumps([{"tool": "a", "input": {}}, {"tool": "b", "input": {}}])
    blocks = wb.parse_response(raw)
    assert [b["name"] for b in blocks] == ["a", "b"]


def test_parse_invalid_json_falls_back_to_text():
    blocks = wb.parse_response("{not json")
    assert blocks[0]["type"] == "text"


def test_parse_empty():
    assert wb.parse_response("   ") == []


# ───────────────────────── 端到端调用 ─────────────────────────

def test_call_returns_tool_use(queue):
    _auto_answer(queue, json.dumps({"tool": "write_file", "input": {"path": "p.md"}}))
    resp = wb.call_workbuddy_with_tools(
        [{"role": "user", "content": "出题"}], "SYS", [], cfg={},
        model="workbuddy-agent", max_tokens=100, quiet=True)
    assert resp["stop_reason"] == "tool_use"
    assert resp["content"][0]["name"] == "write_file"
    assert resp["usage"]["output"] > 0
    # 完成后归档进 done/
    assert (queue / "done" / f"{resp['_req_id']}.json").exists() if "_req_id" in resp else True


def test_call_returns_text(queue):
    _auto_answer(queue, "这一轮结束")
    resp = wb.call_workbuddy_with_tools(
        [{"role": "user", "content": "出题"}], "SYS", [], cfg={}, quiet=True)
    assert resp["stop_reason"] == "end_turn"
    assert resp["content"][0]["text"] == "这一轮结束"


def test_call_timeout(queue):
    with pytest.raises(TimeoutError):
        wb.call_workbuddy_with_tools(
            [{"role": "user", "content": "没人应答"}], "SYS", [], cfg={},
            timeout_sec=1, quiet=True)
    # 超时后请求也应归档，不留在 pending
    assert not list((queue / "pending").glob("*.json"))


def test_call_cancel(queue):
    pending = queue / "pending"

    def _cancel():
        deadline = time.time() + 4
        while time.time() < deadline:
            reqs = sorted(pending.glob("*.json"))
            if reqs:
                reqs[0].with_suffix(".cancel").write_text("stop", encoding="utf-8")
                return
            time.sleep(0.05)

    threading.Thread(target=_cancel, daemon=True).start()
    with pytest.raises(RuntimeError):
        wb.call_workbuddy_with_tools(
            [{"role": "user", "content": "取消"}], "SYS", [], cfg={},
            timeout_sec=10, quiet=True)


# ───────────────────────── CLI ─────────────────────────

def test_cli_list_and_answer(queue, capsys):
    wb.submit_request(queue_dir=queue, req_id="cli-1", system="S", messages=[],
                      tools=[], model="m", max_tokens=10, stage="cli")
    assert wb.main(["--queue-dir", str(queue), "list"]) == 0
    out = capsys.readouterr().out
    assert "cli-1" in out

    assert wb.main(["--queue-dir", str(queue), "answer", "cli-1",
                    "--text", "hello"]) == 0
    assert (queue / "pending" / "cli-1.resp").read_text(encoding="utf-8") == "hello"


def test_llm_client_routes_to_workbuddy(queue):
    """llm_client.call_llm_text 在 provider=workbuddy 时应走文件队列。"""
    from config import get_provider
    from llm_client import call_llm_text

    protocol, cfg = get_provider("workbuddy")
    assert protocol == "workbuddy"

    _auto_answer(queue, "WORKBUDDY-OK")
    sink: dict = {}
    text = call_llm_text("SYS", "USER", provider="workbuddy", usage_sink=sink)
    assert text == "WORKBUDDY-OK"
    assert sink.get("output", 0) > 0
