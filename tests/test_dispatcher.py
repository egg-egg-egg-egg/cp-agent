import pipeline
from pipeline import execute_tool


def test_generate_test_data_count_passthrough(tmp_problem_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "tool_generate_test_data",
                        lambda pd, count: seen.setdefault("count", count) or {"success": True})
    execute_tool(tmp_problem_dir, "generate_test_data", {"count": 5})
    assert seen["count"] == 5


def test_run_solution_timeout_passthrough(tmp_problem_dir, monkeypatch):
    seen = {}

    def fake(pd, timeout_sec=None):
        seen["timeout_sec"] = timeout_sec
        return {"success": True}

    monkeypatch.setattr(pipeline, "tool_run_solution", fake)
    execute_tool(tmp_problem_dir, "run_solution", {"timeout_sec": 9})
    assert seen["timeout_sec"] == 9
    execute_tool(tmp_problem_dir, "run_solution", {})
    assert seen["timeout_sec"] is None


def test_stress_count_passthrough(tmp_problem_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "tool_stress_test",
                        lambda pd, count: seen.setdefault("count", count) or {"success": True})
    execute_tool(tmp_problem_dir, "stress_test", {"count": 77})
    assert seen["count"] == 77


def test_unknown_tool(tmp_problem_dir):
    r = execute_tool(tmp_problem_dir, "not_a_tool", {})
    assert r["success"] is False
    assert "未知工具" in r["message"]


def test_tool_exception_caught(tmp_problem_dir, monkeypatch):
    def boom(pd, count):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "tool_generate_test_data", boom)
    r = execute_tool(tmp_problem_dir, "generate_test_data", {"count": 1})
    assert r["success"] is False
    assert "boom" in r["message"]
