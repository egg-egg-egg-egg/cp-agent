"""agent_loop gating tests with a scripted fake LLM (no network)."""
import agent as agent_mod
from agent import agent_loop

CHINESE_MD = "# 求和问题\n\n" + "给定一个整数序列，求所有元素之和。输入包含多个测试点，每个测试点第一行是序列长度，第二行是序列元素。输出每个测试点的和。数据保证序列长度不超过十万，元素绝对值不超过一亿。这是一道非常基础的入门题目，主要考察输入输出。\n"


def _tool_use(name, args=None):
    return {"type": "tool_use", "id": f"id_{name}", "name": name, "input": args or {}}


def _scripted_llm(responses):
    it = iter(responses)

    def fake(messages, system, provider, model=None, base_url=None,
             api_key=None, max_tokens=16000):
        return {"stop_reason": "x", "content": next(it), "usage": {"input": 1, "output": 1}}

    return fake


def test_dup_gate_blocks_and_unblocks(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")

    search_results = iter([
        {"success": True, "dup_verdict": "must_change", "message": "撞题", "results": []},
        {"success": True, "dup_verdict": "ok", "message": "ok", "results": []},
    ])
    monkeypatch.setattr(agent_mod, "_search_problem_db",
                        lambda q, k=8: next(search_results))

    executed = []
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: executed.append(name) or {"success": True, "message": "ok"})

    responses = [
        [_tool_use("search_problem_db", {"query": "lis"})],   # verdict: must_change
        [_tool_use("generate_test_data", {"count": 3})],      # must be blocked
        [{"type": "text", "text": "总结完成"}],                # completion gate: still must_change
        [_tool_use("search_problem_db", {"query": "new"})],   # verdict: ok
        [{"type": "text", "text": "总结完成"}],                # gates pass → final_check runs → done
    ]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))

    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek",
                   max_iterations=10)
    assert r["success"] is True
    # generate_test_data was blocked (never executed); final_check ran server-side
    assert "generate_test_data" not in executed
    assert executed == ["final_check"]
    assert r["iterations"] == 5


def test_completion_requires_search(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    monkeypatch.setattr(agent_mod, "_search_problem_db",
                        lambda q, k=8: {"success": True, "dup_verdict": "ok", "results": []})

    responses = [
        [{"type": "text", "text": "总结"}],                    # blocked: never searched
        [_tool_use("search_problem_db", {"query": "q"})],
        [{"type": "text", "text": "总结"}],                    # now passes
    ]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))
    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek", max_iterations=5)
    assert r["success"] is True
    assert r["iterations"] == 3


def test_completion_blocked_by_final_check(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db",
                        lambda q, k=8: {"success": True, "dup_verdict": "ok", "results": []})

    fc_results = iter([
        {"success": False, "message": "final_check 未通过", "problems": ["缺少文件 problem.yaml"]},
        {"success": True, "message": "ok"},
    ])
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: next(fc_results))

    responses = [
        [_tool_use("search_problem_db", {"query": "q"})],
        [{"type": "text", "text": "总结"}],                    # final_check fails → retry
        [{"type": "text", "text": "总结"}],                    # final_check passes
    ]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))
    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek", max_iterations=5)
    assert r["success"] is True
    assert r["iterations"] == 3
    # the retry prompt should mention the missing file
    retry_msgs = [m for m in r["messages"] if m["role"] == "user" and "problem.yaml" in str(m.get("content", ""))]
    assert retry_msgs
