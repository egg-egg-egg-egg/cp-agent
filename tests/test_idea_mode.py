"""题意完善模式：prompt 分支与 dedup_policy 三策略（脚本化假 LLM，不联网）。"""
import agent as agent_mod
from agent import agent_loop, build_user_prompt

IDEA = "给一棵树，每次删一条边，问剩余连通块内第 k 大，强制在线"
CHINESE_MD = "# 树上第k大\n\n" + "给定一棵树和若干操作，每次删除一条边后询问某个连通块内的第k大值，要求强制在线处理。数据规模十万级别，需要主席树与启发式合并配合完成，本题综合考察数据结构功底。" * 2


def _tool_use(name, args=None):
    return {"type": "tool_use", "id": f"id_{name}", "name": name, "input": args or {}}


def _scripted_llm(responses):
    it = iter(responses)

    def fake(messages, system, provider, model=None, base_url=None,
             api_key=None, max_tokens=16000):
        return {"stop_reason": "x", "content": next(it), "usage": {"input": 1, "output": 1}}

    return fake


# ─── build_user_prompt ───────────────────────────────────────────────────────

def test_prompt_idea_branch(tmp_config):
    p = build_user_prompt("", 2100, idea=IDEA)
    assert IDEA in p
    assert "不得改变" in p and "核心题目模型" in p
    assert "不要自行换题" in p


def test_prompt_idea_with_topic_hint(tmp_config):
    p = build_user_prompt("dp", 1800, idea=IDEA)
    assert "参考算法考点" in p


def test_prompt_free_mode_unchanged(tmp_config):
    p = build_user_prompt("dp", 1500)
    assert "请生成一道算法竞赛题目" in p
    assert "题意" not in p.split("语言要求")[0]


# ─── dedup_policy ────────────────────────────────────────────────────────────

def _must_change_result():
    return {"success": True, "dup_verdict": "must_change", "message": "撞题",
            "duplicates": [{"title": "原题A", "source": "luogu", "source_id": "P1",
                            "url": "", "vector_score": 0.7, "reason": "同模型"}],
            "results": []}


def test_abort_policy_stops_immediately(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_dedup_check",
                        lambda pd, q, k=8, llm_kwargs=None: _must_change_result())
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    responses = [[_tool_use("search_problem_db", {"query": "q"})]]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))

    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek",
                   max_iterations=10, dedup_policy="abort")
    assert r["success"] is False
    assert r["aborted_dup"] is True
    assert r["iterations"] == 1
    assert "原题A" in r["summary"]
    assert "--allow-dup" in r["summary"]


def test_warn_policy_continues_without_gating(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_dedup_check",
                        lambda pd, q, k=8, llm_kwargs=None: _must_change_result())
    executed = []
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: executed.append(name) or {"success": True, "message": "ok"})
    responses = [
        [_tool_use("search_problem_db", {"query": "q"})],
        [_tool_use("generate_test_data", {"count": 3})],   # 不应被拦截
        [{"type": "text", "text": "总结"}],                # 完成门禁应放行（verdict 已降级）
    ]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))

    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek",
                   max_iterations=10, dedup_policy="warn")
    assert r["success"] is True
    assert "generate_test_data" in executed


def test_rewrite_policy_still_gates(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(CHINESE_MD, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_dedup_check",
                        lambda pd, q, k=8, llm_kwargs=None: _must_change_result())
    executed = []
    monkeypatch.setattr(agent_mod, "execute_tool",
                        lambda pd, name, args: executed.append(name) or {"success": True, "message": "ok"})
    responses = [
        [_tool_use("search_problem_db", {"query": "q"})],
        [_tool_use("generate_test_data", {"count": 3})],   # rewrite 策略下应被拦截
        [{"type": "text", "text": "总结"}],
    ]
    monkeypatch.setattr(agent_mod, "call_llm_with_tools", _scripted_llm(responses))

    r = agent_loop("prompt", problem_dir=tmp_problem_dir, provider="deepseek",
                   max_iterations=3, dedup_policy="rewrite")
    assert "generate_test_data" not in executed
    assert r["success"] is False  # 完成门禁 must_change，最终到 max_iterations


# ─── generate_problem 参数与半成品目录 ────────────────────────────────────────

def test_generate_requires_topic_or_idea(tmp_config):
    import pytest

    from agent import generate_problem
    with pytest.raises(ValueError, match="topic 与 idea"):
        generate_problem(topic="", idea="")


def test_existing_files_injected_into_prompt(tmp_config, tmp_path, monkeypatch):
    from agent import generate_problem
    problems_root = tmp_path / "problems"
    monkeypatch.setattr(agent_mod, "PROBLEMS_DIR", problems_root)
    draft_dir = problems_root / "draft1"
    draft_dir.mkdir(parents=True)
    (draft_dir / "problem.md").write_text("# 草稿", encoding="utf-8")
    (draft_dir / "solution.cpp").write_text("int main(){}", encoding="utf-8")

    captured = {}

    def fake_loop(user_prompt, **kwargs):
        captured["prompt"] = user_prompt
        captured["dedup_policy"] = kwargs.get("dedup_policy")
        return {"success": False, "iterations": 1, "messages": [], "summary": "stub",
                "tokens": {}, "tool_stats": {}}

    monkeypatch.setattr(agent_mod, "agent_loop", fake_loop)
    generate_problem(idea=IDEA, problem_name="draft1")
    assert "problem.md" in captured["prompt"]
    assert "solution.cpp" in captured["prompt"]
    assert "增量补全" in captured["prompt"]
    assert captured["dedup_policy"] == "abort"
    # 含用户草稿的目录失败时必须原地保留，不得归档挪走
    assert draft_dir.exists()
    assert (draft_dir / "problem.md").read_text(encoding="utf-8") == "# 草稿"
    assert (draft_dir / "result.json").exists()
    assert not (problems_root / "failed").exists()


def test_allow_dup_maps_to_warn_policy(tmp_config, tmp_path, monkeypatch):
    from agent import generate_problem
    monkeypatch.setattr(agent_mod, "PROBLEMS_DIR", tmp_path / "problems")
    captured = {}

    def fake_loop(user_prompt, **kwargs):
        captured["dedup_policy"] = kwargs.get("dedup_policy")
        return {"success": False, "iterations": 1, "messages": [], "summary": "stub",
                "tokens": {}, "tool_stats": {}}

    monkeypatch.setattr(agent_mod, "agent_loop", fake_loop)
    generate_problem(idea=IDEA, allow_dup=True, problem_name="x1")
    assert captured["dedup_policy"] == "warn"
    generate_problem(topic="dp", problem_name="x2")
    assert captured["dedup_policy"] == "rewrite"
