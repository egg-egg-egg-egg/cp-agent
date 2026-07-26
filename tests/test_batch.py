import json
import subprocess
import types

import batch_generate


def _fake_run(rc=0):
    def fake(cmd, cwd=None, capture_output=True, text=True, timeout=None):
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")
    return fake


def _setup(tmp_path, monkeypatch):
    problems = tmp_path / "problems"
    problems.mkdir()
    monkeypatch.setattr(batch_generate, "PROBLEMS_DIR", problems)
    monkeypatch.setattr(batch_generate, "LOG_FILE", tmp_path / "batch.log")
    monkeypatch.setattr(subprocess, "run", _fake_run())
    return problems


def test_success_from_result_json(tmp_path, monkeypatch):
    problems = _setup(tmp_path, monkeypatch)
    d = problems / "p1"
    d.mkdir()
    (d / "result.json").write_text(json.dumps({"success": True, "iterations": 5}))
    ok, info = batch_generate.run_one_problem("dp", 1500, "p1")
    assert ok is True
    assert info["iterations"] == 5


def test_failure_from_result_json(tmp_path, monkeypatch):
    problems = _setup(tmp_path, monkeypatch)
    d = problems / "failed" / "p1_20260726_120000"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"success": False, "failure_reason": "产物不完整"}))
    ok, info = batch_generate.run_one_problem("dp", 1500, "p1")
    assert ok is False
    assert "产物不完整" in info["failure_reason"]
    assert "failed" in info["result_path"]


def test_missing_result_json_is_failure(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ok, info = batch_generate.run_one_problem("dp", 1500, "p1")
    assert ok is False
    assert "result.json" in info["failure_reason"]


def test_rc_nonzero_is_failure_even_with_success_json(tmp_path, monkeypatch):
    problems = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, "run", _fake_run(rc=1))
    d = problems / "p1"
    d.mkdir()
    (d / "result.json").write_text(json.dumps({"success": True}))
    ok, _ = batch_generate.run_one_problem("dp", 1500, "p1")
    assert ok is False
