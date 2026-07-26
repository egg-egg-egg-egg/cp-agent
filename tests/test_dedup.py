"""LLM-judge dedup tests (retrieval and judge both mocked, no network)."""
import json

import pytest

import agent as agent_mod
from agent import _dedup_check, _parse_judge_response, _threshold_fallback_verdict

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
    j = _parse_judge_response(text)
    assert j[0]["same_model"] is True
    with pytest.raises(ValueError):
        _parse_judge_response("没有 json")
    with pytest.raises(ValueError):
        _parse_judge_response('{"foo": 1}')


def test_threshold_fallback():
    assert _threshold_fallback_verdict(0.9)[0] == "must_change"
    assert _threshold_fallback_verdict(0.75)[0] == "manual_review"
    assert _threshold_fallback_verdict(0.5)[0] == "ok"


def test_below_trigger_skips_judge(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db", lambda q, k=8: _retrieval([0.4, 0.3]))
    monkeypatch.setattr(agent_mod, "_call_llm_text",
                        lambda *a, **k: pytest.fail("judge should not be called"))
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"
    assert "触发线" in r["message"]


def test_judge_confirms_duplicate(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db", lambda q, k=8: _retrieval([0.67, 0.62]))
    seen = {}

    def fake_judge(system, user, **kwargs):
        seen["user"] = user
        return json.dumps({"judgements": [
            {"index": 1, "same_model": True, "reason": "同一题目模型"},
            {"index": 2, "same_model": False, "reason": "只是同算法"},
        ]})

    monkeypatch.setattr(agent_mod, "_call_llm_text", fake_judge)
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "must_change"
    assert len(r["duplicates"]) == 1
    assert r["duplicates"][0]["title"] == "题目0"
    assert "最长上升子序列" in seen["user"]      # statement included
    assert "候选 2" in seen["user"]              # both candidates sent


def test_judge_clears_high_scores(tmp_problem_dir, tmp_config, monkeypatch):
    """High retrieval score but judge says distinct → ok (score is not the gate)."""
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db", lambda q, k=8: _retrieval([0.97]))
    monkeypatch.setattr(agent_mod, "_call_llm_text", lambda *a, **k: json.dumps(
        {"judgements": [{"index": 1, "same_model": False, "reason": "问题模型不同"}]}))
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"


def test_judge_failure_falls_back_to_thresholds(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db", lambda q, k=8: _retrieval([0.96]))

    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(agent_mod, "_call_llm_text", boom)
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "must_change"     # 0.96 → threshold fallback
    assert "judge_error" in r


def test_missing_statement_defers_judging(tmp_problem_dir, tmp_config, monkeypatch):
    monkeypatch.setattr(agent_mod, "_search_problem_db", lambda q, k=8: _retrieval([0.7]))
    monkeypatch.setattr(agent_mod, "_call_llm_text",
                        lambda *a, **k: pytest.fail("judge needs the statement"))
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "manual_review"
    assert "problem.md" in r["message"]


def test_max_candidates_cap(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "_search_problem_db",
                        lambda q, k=8: _retrieval([0.9, 0.8, 0.75, 0.7, 0.68, 0.65, 0.62]))
    seen = {}

    def fake_judge(system, user, **kwargs):
        seen["user"] = user
        return json.dumps({"judgements": [
            {"index": i, "same_model": False, "reason": "不同"} for i in range(1, 6)]})

    monkeypatch.setattr(agent_mod, "_call_llm_text", fake_judge)
    r = _dedup_check(tmp_problem_dir, "q")
    assert r["dup_verdict"] == "ok"
    assert "候选 5" in seen["user"]
    assert "候选 6" not in seen["user"]          # capped at 5
