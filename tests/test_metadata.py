import yaml

from pipeline import tool_write_metadata


def _fill(problem_dir, n=2):
    for i in range(1, n + 1):
        (problem_dir / "inputs" / f"{i:02d}.in").write_text("1\n")
        (problem_dir / "outputs" / f"{i:02d}.out").write_text("2\n")


def test_write_metadata_basic(tmp_problem_dir, tmp_config):
    _fill(tmp_problem_dir)
    r = tool_write_metadata(tmp_problem_dir, "测试题", ["动态规划"], difficulty=1500,
                            provider="openai.deepseek")
    assert r["success"] is True
    meta = yaml.safe_load((tmp_problem_dir / "problem.yaml").read_text(encoding="utf-8"))
    assert meta["title"] == "测试题"
    assert meta["slug"] == tmp_problem_dir.name
    assert meta["difficulty"] == 1500
    assert meta["time_limit_ms"] == 1000       # config default
    assert meta["memory_limit_mb"] == 256
    assert meta["checker"]["type"] == "builtin"
    assert len(meta["cases"]) == 2
    assert meta["cases"][0] == {"input": "inputs/01.in", "output": "outputs/01.out"}
    assert meta["generated"]["provider"] == "openai.deepseek"


def test_write_metadata_detects_checker(tmp_problem_dir, tmp_config):
    _fill(tmp_problem_dir)
    (tmp_problem_dir / "checker.cpp").write_text("// spj")
    r = tool_write_metadata(tmp_problem_dir, "构造题", ["构造"])
    assert r["checker_type"] == "testlib"


def test_write_metadata_missing_output_fails(tmp_problem_dir, tmp_config):
    _fill(tmp_problem_dir)
    (tmp_problem_dir / "outputs" / "02.out").unlink()
    r = tool_write_metadata(tmp_problem_dir, "题", ["dp"])
    assert r["success"] is False
    assert "02.out" in r["message"]


def test_write_metadata_validation_block_preserved(tmp_problem_dir, tmp_config):
    _fill(tmp_problem_dir)
    tool_write_metadata(tmp_problem_dir, "题", ["dp"])
    meta = yaml.safe_load((tmp_problem_dir / "problem.yaml").read_text(encoding="utf-8"))
    meta["validation"] = {"stress": {"passed": True}}
    (tmp_problem_dir / "problem.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True))
    tool_write_metadata(tmp_problem_dir, "新标题", ["dp"])
    meta2 = yaml.safe_load((tmp_problem_dir / "problem.yaml").read_text(encoding="utf-8"))
    assert meta2["title"] == "新标题"
    assert meta2["validation"] == {"stress": {"passed": True}}


def test_write_metadata_rejects_empty_fields(tmp_problem_dir, tmp_config):
    assert tool_write_metadata(tmp_problem_dir, "", ["dp"])["success"] is False
    assert tool_write_metadata(tmp_problem_dir, "题", [])["success"] is False
