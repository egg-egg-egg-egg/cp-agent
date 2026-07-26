"""LLM-judge dedup tests (retrieval and judge both mocked, no network)."""
import json

import pytest

import dedup as dedup_mod
from dedup import dedup_check, parse_judge_response, threshold_fallback_verdict

STATEMENT = "# 最长上升子序列\n\n给定序列，求最长严格上升子序列长度。" * 3


def _retrieval(vector_scores):
    return {
        "success": True,
        "message": "本地题库检索完成",
        "query": "q",
        "top_vector_score": max(vector_scores, default=0.0),
        "results": [
            {"rank": i + 1, "final_score": 0.09, "vector_score": s, "source": "codeforces",
             "source_id": f"cf{i}", "title": f"题目{i}", "url": "", "tags": "",
             "difficulty": "", "matched_terms": [], "term_coverage": 0,
             "term_strength": 0, "snippet": "求最长上升子序列"}
            for i, s in enumerate(vector_scores)
        ],
    }


def test_parse_judge_response():
    text = '前置废话 {"judgements": [{"index": 1, "same_model": true, "reason": "同为LIS"}]} 后缀'
    j = parse_judge_response(text)
    assert j[0]["same_model"] is True
    with pytest.raises(ValueError):
        parse_judge_response("没有 json")
    with pytest.raises(ValueError):
        parse_judge_response('{"foo": 1}')


def test_threshold_fallback():
    assert threshold_fallback_verdict(0.9)[0] == "must_change"
    assert threshold_fallback_verdict(0.75)[0] == "manual_review"
    assert threshold_fallback_verdict(0.5)[0] == "ok"


def test_below_trigger_skips_judge(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.4, 0.3]))
    monkeypatch.setattr(dedup_mod, "call_llm_text",
                        lambda *a, **k: pytest.fail("judge should not be called"))
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"
    assert "触发线" in r["message"]


def test_judge_confirms_duplicate(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.67, 0.62]))
    seen = {}

    def fake_judge(system, user, **kwargs):
        seen["user"] = user
        return json.dumps({"judgements": [
            {"index": 1, "same_model": True, "reason": "同一题目模型"},
            {"index": 2, "same_model": False, "reason": "只是同算法"},
        ]})

    monkeypatch.setattr(dedup_mod, "call_llm_text", fake_judge)
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "must_change"
    assert len(r["duplicates"]) == 1
    assert r["duplicates"][0]["title"] == "题目0"
    assert "最长上升子序列" in seen["user"]      # statement included
    assert "候选 2" in seen["user"]              # both candidates sent


def test_judge_clears_high_scores(tmp_problem_dir, tmp_config, monkeypatch):
    """High retrieval score but judge says distinct → ok (score is not the gate)."""
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.97]))
    monkeypatch.setattr(dedup_mod, "call_llm_text", lambda *a, **k: json.dumps(
        {"judgements": [{"index": 1, "same_model": False, "reason": "问题模型不同"}]}))
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"


def test_judge_failure_falls_back_to_thresholds(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.96]))

    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(dedup_mod, "call_llm_text", boom)
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "must_change"     # 0.96 → threshold fallback
    assert "judge_error" in r


def test_missing_statement_defers_judging(tmp_problem_dir, tmp_config, monkeypatch):
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.7]))
    monkeypatch.setattr(dedup_mod, "call_llm_text",
                        lambda *a, **k: pytest.fail("judge needs the statement"))
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "manual_review"
    assert "problem.md" in r["message"]


def test_statement_recall_queries():
    from dedup import statement_recall_queries
    md = "# 标题A\n\n## 题目描述\n\n第一段描述内容。\n第二行内容。\n\n```\n代码块跳过\n```\n"
    qs = statement_recall_queries(md)
    assert qs[0] == "标题A"
    assert "第一段描述内容" in qs[1]
    assert "代码块" not in qs[1]


def test_statement_recall_merges_and_rescues(tmp_problem_dir, tmp_config, monkeypatch):
    """关键词召回弱、标题/描述路召回强时，多路合并应让裁判仍能看到高分候选。"""
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    calls = []

    def fake_search(q, k=8):
        calls.append(q)
        if len(calls) == 1:          # 关键词路：全部低于触发线
            return _retrieval([0.2, 0.1])
        return _retrieval([0.72])    # 标题/描述路：召回高分候选（同 source_id 取高分）

    monkeypatch.setattr(dedup_mod, "search_problem_db", fake_search)
    monkeypatch.setattr(dedup_mod, "call_llm_text", lambda *a, **k: json.dumps(
        {"judgements": [{"index": 1, "same_model": True, "reason": "同模型"}]}))
    r = dedup_check(tmp_problem_dir, "写得很差的query")
    assert len(calls) == 3               # 关键词 + 标题 + 描述首段
    assert calls[1] == "最长上升子序列"   # 标题路
    assert r["dup_verdict"] == "must_change"
    assert r["top_vector_score"] == 0.72


def test_judge_model_override_and_usage_sink(tmp_problem_dir, tmp_config, monkeypatch):
    """配置 dedup_judge_model 时裁判走独立 provider；token 用量写入 judge_tokens。"""
    tmp_config.write_text(tmp_config.read_text() + '\ndedup_judge_model: "openai.deepseek"\n',
                          encoding="utf-8")
    import config
    config.load_config.cache_clear()

    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db", lambda q, k=8: _retrieval([0.7]))
    seen = {}

    def fake_judge(system, user, usage_sink=None, **kwargs):
        seen["kwargs"] = kwargs
        if usage_sink is not None:
            usage_sink["input"] = 123
            usage_sink["output"] = 45
        return json.dumps({"judgements": [{"index": 1, "same_model": False, "reason": "不同"}]})

    monkeypatch.setattr(dedup_mod, "call_llm_text", fake_judge)
    r = dedup_check(tmp_problem_dir, "q",
                    llm_kwargs={"provider": "main_provider", "model": "big-model"})
    assert seen["kwargs"] == {"provider": "openai.deepseek"}   # 主 provider 参数被替换
    assert r["judge_tokens"] == {"input": 123, "output": 45}


def test_max_candidates_cap(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(dedup_mod, "search_problem_db",
                        lambda q, k=8: _retrieval([0.9, 0.8, 0.75, 0.7, 0.68, 0.65, 0.62]))
    seen = {}

    def fake_judge(system, user, **kwargs):
        seen["user"] = user
        return json.dumps({"judgements": [
            {"index": i, "same_model": False, "reason": "不同"} for i in range(1, 6)]})

    monkeypatch.setattr(dedup_mod, "call_llm_text", fake_judge)
    r = dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"
    assert "候选 5" in seen["user"]
    assert "候选 6" not in seen["user"]          # capped at 5
