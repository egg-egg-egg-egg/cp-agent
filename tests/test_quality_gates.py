import pipeline
from pipeline import (
    _extract_samples,
    _parse_bounds_log,
    tool_check_data_strength,
    tool_validate_inputs,
)


def test_parse_bounds_log():
    log = '"n": min-value-hit max-value-hit\n"a[i]": min-value-hit\n"m":\n'
    r = _parse_bounds_log(log)
    assert r["n"] == {"min_hit": True, "max_hit": True}
    assert r["a[i]"] == {"min_hit": True, "max_hit": False}
    assert r["m"] == {"min_hit": False, "max_hit": False}


def test_extract_samples():
    md = (
        "# 题目\n\n## 样例输入 1\n\n```\n3\n1 2 3\n```\n\n"
        "## 样例输出 1\n\n```\n6\n```\n\n"
        "### 样例输入 2\n```text\n1\n5\n```\n### 样例输出 2\n```\n5\n```\n"
    )
    samples = _extract_samples(md)
    assert len(samples) == 2
    assert samples[0] == ("3\n1 2 3\n", "6\n")
    assert samples[1][1] == "5\n"


def _write_validator(problem_dir, new_style):
    src = "registerValidation(argc, argv);" if new_style else "registerGen(argc, argv, 1);"
    (problem_dir / "validator.cpp").write_text(f"int main() {{ {src} }}")
    (problem_dir / "bin" / "validator").write_text("")


def test_validate_inputs_new_style_bounds(tmp_problem_dir, tmp_config, monkeypatch):
    _write_validator(tmp_problem_dir, new_style=True)
    (tmp_problem_dir / "inputs" / "01.in").write_text("1\n")
    (tmp_problem_dir / "inputs" / "02.in").write_text("100000\n")

    logs = iter(['"n": min-value-hit\n', '"n": max-value-hit\n'])

    def fake_run(cmd, cwd=".", timeout=60, stdin_data=None, **kwargs):
        log_arg = next(a for a in cmd if "--testOverviewLogFileName=" in a)
        with open(log_arg.split("=", 1)[1], "w") as f:
            f.write(next(logs))
        return 0, "", ""

    monkeypatch.setattr(pipeline, "_run_cmd", fake_run)
    r = tool_validate_inputs(tmp_problem_dir)
    assert r["success"] is True
    assert r["bounds_unhit"] == []            # merged across test cases
    assert r["bounds_report"]["n"] == {"min_hit": True, "max_hit": True}


def test_validate_inputs_reports_unhit_bounds(tmp_problem_dir, tmp_config, monkeypatch):
    _write_validator(tmp_problem_dir, new_style=True)
    (tmp_problem_dir / "inputs" / "01.in").write_text("5\n")

    def fake_run(cmd, cwd=".", timeout=60, stdin_data=None, **kwargs):
        log_arg = next(a for a in cmd if "--testOverviewLogFileName=" in a)
        with open(log_arg.split("=", 1)[1], "w") as f:
            f.write('"n": min-value-hit\n')
        return 0, "", ""

    monkeypatch.setattr(pipeline, "_run_cmd", fake_run)
    r = tool_validate_inputs(tmp_problem_dir)
    assert r["success"] is True               # warning only at validate stage
    assert r["bounds_unhit"] == ["n.max"]


def test_validate_inputs_old_style_no_bounds(tmp_problem_dir, tmp_config, monkeypatch):
    _write_validator(tmp_problem_dir, new_style=False)
    (tmp_problem_dir / "inputs" / "01.in").write_text("5\n")
    seen_cmds = []

    def fake_run(cmd, cwd=".", timeout=60, stdin_data=None, **kwargs):
        seen_cmds.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(pipeline, "_run_cmd", fake_run)
    r = tool_validate_inputs(tmp_problem_dir)
    assert r["success"] is True
    assert r["bounds_report"] is None
    assert seen_cmds[0][1].endswith("01.in")  # old convention: file path argv


def _strength_setup(problem_dir):
    for name in ("solution", "naive"):
        (problem_dir / "bin" / name).write_text("")
    (problem_dir / "inputs" / "01.in").write_text("x" * 100)
    (problem_dir / "inputs" / "02.in").write_text("x" * 10)


def test_data_strength_naive_tle_passes(tmp_problem_dir, tmp_config, monkeypatch):
    _strength_setup(tmp_problem_dir)
    # per file: solution ok, naive TLE
    script = iter([(0, "", ""), (-1, "", "TIMEOUT")] * 2)
    monkeypatch.setattr(pipeline, "_run_cmd", lambda *a, **k: next(script))
    r = tool_check_data_strength(tmp_problem_dir)
    assert r["success"] is True
    assert "01.in" in r["naive_tle_on"]


def test_data_strength_weak_data_fails(tmp_problem_dir, tmp_config, monkeypatch):
    _strength_setup(tmp_problem_dir)
    monkeypatch.setattr(pipeline, "_run_cmd", lambda *a, **k: (0, "", ""))
    r = tool_check_data_strength(tmp_problem_dir)
    assert r["success"] is False
    assert "数据太弱" in r["message"]


def test_data_strength_solution_timeout_fails(tmp_problem_dir, tmp_config, monkeypatch):
    _strength_setup(tmp_problem_dir)
    monkeypatch.setattr(pipeline, "_run_cmd", lambda *a, **k: (-1, "", "TIMEOUT"))
    r = tool_check_data_strength(tmp_problem_dir)
    assert r["success"] is False
    assert r.get("solution_timeout") is True
