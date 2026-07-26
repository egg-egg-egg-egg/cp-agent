import json

import pytest
import yaml

pytest.importorskip("PySide6")

from gui import problem_summary_html, scan_one_problem, scan_problems  # noqa: E402


def _make(tmp_path, name, success=True, with_meta=True, archived=False):
    d = (tmp_path / "failed" / name) if archived else (tmp_path / name)
    (d / "inputs").mkdir(parents=True)
    (d / "outputs").mkdir()
    (d / "inputs" / "01.in").write_text("1\n")
    (d / "outputs" / "01.out").write_text("2\n")
    (d / "result.json").write_text(json.dumps(
        {"success": success, "iterations": 7, "elapsed_sec": 12.5,
         "provider": "openai.deepseek", "tokens": {"input": 100, "output": 50},
         "failure_reason": None if success else "产物不完整"}))
    if with_meta:
        (d / "problem.yaml").write_text(yaml.safe_dump(
            {"title": "测试题", "algorithm_tags": ["dp"], "difficulty": 1500,
             "time_limit_ms": 1000, "memory_limit_mb": 256,
             "checker": {"type": "builtin"}}, allow_unicode=True))
    return d


def test_scan_one_ok(tmp_path):
    d = _make(tmp_path, "p1")
    info = scan_one_problem(d)
    assert info["status"] == "ok"
    assert info["title"] == "测试题"
    assert info["inputs"] == info["outputs"] == 1


def test_scan_one_failed_result(tmp_path):
    d = _make(tmp_path, "p2", success=False)
    assert scan_one_problem(d)["status"] == "failed"


def test_scan_one_unknown_without_result(tmp_path):
    d = tmp_path / "p3"
    d.mkdir()
    info = scan_one_problem(d)
    assert info["status"] == "unknown"
    assert info["has_meta"] is False


def test_scan_problems_groups_failed(tmp_path):
    _make(tmp_path, "good")
    _make(tmp_path, "bad_archived", success=False, archived=True)
    entries = scan_problems(tmp_path)
    assert [e["name"] for e in entries] == ["good", "bad_archived"]
    assert entries[0]["archived"] is False
    assert entries[1]["archived"] is True
    assert entries[1]["status"] == "failed"


def test_summary_html(tmp_path):
    info = scan_one_problem(_make(tmp_path, "p1"))
    html = problem_summary_html(info)
    assert "测试题" in html
    assert "1000 ms" in html
    assert "✅ 成功" in html

    info2 = scan_one_problem(_make(tmp_path, "p2", success=False, with_meta=False))
    html2 = problem_summary_html(info2)
    assert "无 problem.yaml" in html2
    assert "产物不完整" in html2
