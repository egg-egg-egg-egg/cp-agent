import pipeline
from pipeline import _token_compare, tool_stress_test


def _make_bins(problem_dir):
    for name in ("solution", "naive", "generator"):
        (problem_dir / "bin" / name).write_text("")


def _scripted_run_cmd(script):
    """Return a fake _run_cmd that pops (returncode, stdout, stderr) per call."""
    calls = []

    def fake(cmd, cwd=".", timeout=60, stdin_data=None):
        calls.append(cmd)
        return script.pop(0) if script else (0, "", "")

    fake.calls = calls
    return fake


def test_token_compare():
    assert _token_compare("1 2 3\n", " 1  2\n3 ")
    assert not _token_compare("1 2", "1 2 3")


def test_naive_timeout_is_failure(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    # gen ok, solution ok, naive timeout (-1)
    fake = _scripted_run_cmd([(0, "3\n", ""), (0, "6\n", ""), (-1, "", "TIMEOUT")])
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    r = tool_stress_test(tmp_problem_dir, count=100)
    assert r["success"] is False
    assert r.get("naive_timeout") is True
    assert "stress" in r["message"]  # instructs generator stress mode


def test_stress_passes_stress_flag_to_generator(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    fake = _scripted_run_cmd([(0, "1\n", ""), (0, "1\n", ""), (0, "1\n", "")])
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    tool_stress_test(tmp_problem_dir, count=1)
    gen_cmd = fake.calls[0]
    assert gen_cmd[-1] == "stress"


def test_mismatch_reports_actual_iterations(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    # round 1 matches, round 2 mismatches, then loop should stop at 3 mismatches max
    script = [
        (0, "in1", ""), (0, "5", ""), (0, "5", ""),      # round 1: match
        (0, "in2", ""), (0, "5", ""), (0, "7", ""),      # round 2: mismatch
        (0, "in3", ""), (0, "5", ""), (0, "8", ""),      # round 3: mismatch
        (0, "in4", ""), (0, "5", ""), (0, "9", ""),      # round 4: mismatch → break
    ]
    fake = _scripted_run_cmd(script)
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    r = tool_stress_test(tmp_problem_dir, count=1000)
    assert r["success"] is False
    assert len(r["mismatches"]) == 3
    assert r["iterations"] == 4  # actual rounds run, not the requested count


def test_solution_timeout_fails(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    fake = _scripted_run_cmd([(0, "in", ""), (-1, "", "TIMEOUT")])
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    r = tool_stress_test(tmp_problem_dir, count=10)
    assert r["success"] is False
    assert r.get("timeout") is True


def test_naive_runtime_error_fails(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    fake = _scripted_run_cmd([(0, "in", ""), (0, "5", ""), (139, "", "segfault")])
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    r = tool_stress_test(tmp_problem_dir, count=10)
    assert r["success"] is False
    assert "naive 运行出错" in r["message"]


def test_all_match_passes(tmp_problem_dir, tmp_config, monkeypatch):
    _make_bins(tmp_problem_dir)
    script = [(0, f"in{i}", "") if j == 0 else (0, "42", "")
              for i in range(2) for j in range(3)]
    fake = _scripted_run_cmd(script)
    monkeypatch.setattr(pipeline, "_run_cmd", fake)
    r = tool_stress_test(tmp_problem_dir, count=2)
    assert r["success"] is True
    assert r["iterations"] == 2
