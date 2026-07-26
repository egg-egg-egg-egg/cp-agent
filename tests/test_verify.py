"""独立验题与难度校准（LLM 与子进程全部 mock）。"""
import json

import pytest

import verify as verify_mod
from verify import cross_solve, extract_cpp, review_difficulty, strip_editorial

STATEMENT = """# 求和

给定 n 个数，求和。

## 题解说明

前缀和即可。
"""


def test_strip_editorial():
    s = strip_editorial(STATEMENT)
    assert "求和" in s
    assert "题解" not in s
    assert "前缀和" not in s


def test_extract_cpp():
    assert extract_cpp("说明\n```cpp\nint main(){}\n```\n完").strip() == "int main(){}"
    assert "include" in extract_cpp("#include <iostream>\nint main(){}")
    with pytest.raises(ValueError):
        extract_cpp("没有代码")


def _setup(problem_dir, n=2):
    (problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")
    for i in range(1, n + 1):
        (problem_dir / "inputs" / f"{i:02d}.in").write_text(f"{i}\n")
        (problem_dir / "outputs" / f"{i:02d}.out").write_text(f"{i * 2}\n")


def _mock_llm(monkeypatch, code="```cpp\nint main(){}\n```"):
    calls = []

    def fake(system, user, usage_sink=None, **kwargs):
        calls.append(user)
        if usage_sink is not None:
            usage_sink["input"] = usage_sink.get("input", 0) + 10
            usage_sink["output"] = usage_sink.get("output", 0) + 5
        return code
    monkeypatch.setattr(verify_mod, "call_llm_text", fake)
    return calls


def test_cross_solve_passed(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir)
    calls = _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    outputs = iter([(0, "2\n", ""), (0, "4\n", "")])
    monkeypatch.setattr(verify_mod, "_run_cmd", lambda *a, **k: next(outputs))

    r = cross_solve(tmp_problem_dir)
    assert r["status"] == "passed"
    assert r["verified"] == 2
    assert r["tokens"] == {"input": 10, "output": 5}
    assert "题解" not in calls[0]        # 验题人看不到题解
    assert (tmp_problem_dir / "cross_solution.cpp").exists()


def test_cross_solve_mismatch_fails(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir)
    _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    outputs = iter([(0, "2\n", ""), (0, "999\n", "")])
    monkeypatch.setattr(verify_mod, "_run_cmd", lambda *a, **k: next(outputs))

    r = cross_solve(tmp_problem_dir)
    assert r["status"] == "failed"
    assert r["mismatches"][0]["file"] == "02.in"


def test_cross_solve_tle_is_skipped_not_failed(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir)
    _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    outputs = iter([(-1, "", "TIMEOUT"), (0, "4\n", "")])
    monkeypatch.setattr(verify_mod, "_run_cmd", lambda *a, **k: next(outputs))

    r = cross_solve(tmp_problem_dir)
    assert r["status"] == "passed"
    assert r["verified"] == 1
    assert r["skipped"][0]["why"] == "TLE"


def test_cross_solve_all_tle_inconclusive(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir)
    _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    monkeypatch.setattr(verify_mod, "_run_cmd", lambda *a, **k: (-1, "", "TIMEOUT"))
    assert cross_solve(tmp_problem_dir)["status"] == "inconclusive"


def test_cross_solve_compile_retry_then_inconclusive(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir)
    calls = _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": False, "message": "error: xxx"})
    r = cross_solve(tmp_problem_dir)
    assert r["status"] == "inconclusive"
    assert r["attempts"] == 2
    assert "编译失败" in calls[1]        # 第二次带了编译错误反馈


def test_cross_solve_uses_checker_when_present(tmp_problem_dir, tmp_config, monkeypatch):
    _setup(tmp_problem_dir, n=1)
    (tmp_problem_dir / "bin" / "checker").write_text("")
    _mock_llm(monkeypatch)
    monkeypatch.setattr(verify_mod, "execute_tool",
                        lambda pd, name, args: {"success": True, "message": "ok"})
    monkeypatch.setattr(verify_mod, "_run_cmd", lambda *a, **k: (0, "different\n", ""))
    monkeypatch.setattr(verify_mod, "_run_checker", lambda *a: ("AC", "ok"))
    assert cross_solve(tmp_problem_dir)["status"] == "passed"   # checker 判 AC 即通过


def test_review_difficulty_ok_and_warning(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")

    monkeypatch.setattr(verify_mod, "call_llm_text",
                        lambda *a, **k: json.dumps({"estimated": 1600, "reason": "基础前缀和"}))
    r = review_difficulty(tmp_problem_dir, 1500)
    assert r["status"] == "ok" and r["delta"] == 100

    monkeypatch.setattr(verify_mod, "call_llm_text",
                        lambda *a, **k: json.dumps({"estimated": 2400, "reason": "远超标称"}))
    r = review_difficulty(tmp_problem_dir, 1500)
    assert r["status"] == "warning" and r["delta"] == 900


def test_review_difficulty_error_is_soft(tmp_problem_dir, tmp_config, monkeypatch):
    (tmp_problem_dir / "problem.md").write_text(STATEMENT, encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(verify_mod, "call_llm_text", boom)
    assert review_difficulty(tmp_problem_dir, 1500)["status"] == "error"
