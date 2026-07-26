"""generate_problem 对可选验证与入库的接线（全部打桩）。"""
import agent as agent_mod
import dedup as dedup_mod
import verify as verify_mod
from agent import generate_problem


def _complete_dir(root, name):
    d = root / name
    (d / "inputs").mkdir(parents=True)
    (d / "outputs").mkdir()
    (d / "problem.md").write_text("# 题\n\n中文内容")
    (d / "solution.cpp").write_text("int main(){}")
    (d / "inputs" / "01.in").write_text("1\n")
    (d / "outputs" / "01.out").write_text("2\n")
    return d


def _stub_loop(**overrides):
    def fake(user_prompt, **kwargs):
        return {"success": True, "iterations": 3, "messages": [], "summary": "done",
                "tokens": {"input": 100, "output": 50}, "tool_stats": {}, **overrides}
    return fake


def _setup(tmp_path, monkeypatch, name):
    root = tmp_path / "problems"
    monkeypatch.setattr(agent_mod, "PROBLEMS_DIR", root)
    _complete_dir(root, name)
    monkeypatch.setattr(agent_mod, "agent_loop", _stub_loop())
    monkeypatch.setattr(dedup_mod, "index_generated_problem",
                        lambda pd: {"success": True, "message": "已入库"})
    return root


def test_defaults_no_verify_but_indexes(tmp_path, tmp_config, monkeypatch):
    root = _setup(tmp_path, monkeypatch, "p1")
    called = []
    monkeypatch.setattr(verify_mod, "cross_solve",
                        lambda *a, **k: called.append("cross") or {})
    monkeypatch.setattr(dedup_mod, "index_generated_problem",
                        lambda pd: called.append("index") or {"success": True})
    r = generate_problem(topic="dp", problem_name="p1")
    assert r["success"] is True
    assert called == ["index"]                    # 默认：不验题，但入库
    import json
    rj = json.loads((root / "p1" / "result.json").read_text())
    assert rj["indexed"] is True
    assert "cross_check" not in rj


def test_cross_check_failure_fails_run(tmp_path, tmp_config, monkeypatch):
    root = _setup(tmp_path, monkeypatch, "p2")
    monkeypatch.setattr(verify_mod, "cross_solve", lambda pd, kw: {
        "status": "failed", "message": "独立验题不一致", "verified": 1,
        "mismatches": [{"file": "01.in"}], "skipped": [], "attempts": 1,
        "tokens": {"input": 7, "output": 3}})
    indexed = []
    monkeypatch.setattr(dedup_mod, "index_generated_problem",
                        lambda pd: indexed.append(1) or {"success": True})
    r = generate_problem(topic="dp", problem_name="p2", cross_check=True)
    assert r["success"] is False
    assert "独立验题不一致" in r["summary"]
    assert not indexed                            # 验题失败的题不入库
    import json
    rj = json.loads((root / "p2" / "result.json").read_text())
    assert rj["cross_check"]["status"] == "failed"
    assert rj["tokens"] == {"input": 107, "output": 53}   # 验题 token 已并账


def test_difficulty_review_warning_is_soft(tmp_path, tmp_config, monkeypatch):
    root = _setup(tmp_path, monkeypatch, "p3")
    monkeypatch.setattr(verify_mod, "review_difficulty", lambda pd, d, kw: {
        "status": "warning", "declared": 1500, "estimated": 2100, "delta": 600,
        "reason": "偏难", "message": "偏差过大", "tokens": {"input": 5, "output": 2}})
    r = generate_problem(topic="dp", problem_name="p3", difficulty_review=True)
    assert r["success"] is True                   # 警告不判失败
    import json
    rj = json.loads((root / "p3" / "result.json").read_text())
    assert rj["difficulty_review"]["delta"] == 600
