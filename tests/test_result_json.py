import json

from report import check_artifacts, read_result_json, write_result_json


def _fill(problem_dir, inputs=3, outputs=3):
    (problem_dir / "problem.md").write_text("# 题目")
    (problem_dir / "solution.cpp").write_text("int main(){}")
    for i in range(1, inputs + 1):
        (problem_dir / "inputs" / f"{i:02d}.in").write_text("1\n")
    for i in range(1, outputs + 1):
        (problem_dir / "outputs" / f"{i:02d}.out").write_text("1\n")


def test_complete_package(tmp_problem_dir):
    _fill(tmp_problem_dir)
    a = check_artifacts(tmp_problem_dir)
    assert a["complete"] is True
    assert a["inputs"] == a["outputs"] == 3


def test_missing_outputs_incomplete(tmp_problem_dir):
    _fill(tmp_problem_dir, inputs=3, outputs=2)
    a = check_artifacts(tmp_problem_dir)
    assert a["counts_match"] is False
    assert a["complete"] is False


def test_empty_problem_md_incomplete(tmp_problem_dir):
    _fill(tmp_problem_dir)
    (tmp_problem_dir / "problem.md").write_text("")
    assert check_artifacts(tmp_problem_dir)["complete"] is False


def test_no_inputs_incomplete(tmp_problem_dir):
    (tmp_problem_dir / "problem.md").write_text("# 题目")
    (tmp_problem_dir / "solution.cpp").write_text("int main(){}")
    assert check_artifacts(tmp_problem_dir)["complete"] is False


def test_write_and_read_roundtrip(tmp_problem_dir):
    path = write_result_json(tmp_problem_dir, {"success": True, "topic": "dp"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["success"] is True
    assert read_result_json(tmp_problem_dir) == data
    assert not (tmp_problem_dir / "result.json.tmp").exists()


def test_read_missing_returns_none(tmp_problem_dir):
    assert read_result_json(tmp_problem_dir) is None
