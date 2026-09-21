import json
import zipfile
from pathlib import Path

import yaml

from export import export_problem


def _make_problem(problem_dir, spj=False, with_yaml=True):
    (problem_dir / "problem.md").write_text("# 题\n\n## 样例输入\n```\n1\n```\n## 样例输出\n```\n1\n```\n")
    (problem_dir / "solution.cpp").write_text("int main(){}")
    (problem_dir / "naive.cpp").write_text("int main(){}")
    (problem_dir / "generator.cpp").write_text("int main(){}")
    (problem_dir / "validator.cpp").write_text("int main(){}")
    for i in (1, 2):
        (problem_dir / "inputs" / f"{i:02d}.in").write_text(f"{i}\n")
        (problem_dir / "outputs" / f"{i:02d}.out").write_text(f"{i * 2}\n")
    if spj:
        (problem_dir / "checker.cpp").write_text("// spj")
    if with_yaml:
        meta = {
            "title": "测试题", "slug": problem_dir.name,
            "algorithm_tags": ["dp"], "difficulty": 1500,
            "time_limit_ms": 2000, "memory_limit_mb": 512,
            "checker": {"type": "testlib" if spj else "builtin"},
        }
        (problem_dir / "problem.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True))


def test_luogu_flat_zip(tmp_problem_dir, tmp_config):
    _make_problem(tmp_problem_dir)
    export_problem(tmp_problem_dir, ["luogu"])
    zip_path = tmp_problem_dir / "export" / "luogu" / "data.zip"
    names = sorted(zipfile.ZipFile(zip_path).namelist())
    assert names == ["01.in", "01.out", "02.in", "02.out"]   # flat, no directories
    readme = (tmp_problem_dir / "export" / "luogu" / "README.txt").read_text(encoding="utf-8")
    assert "2000ms" in readme


def test_hydrooj_package(tmp_problem_dir, tmp_config):
    """HydroOJ 平台包：problem.json / problem.yaml / problem_zh.md / testdata 全套。"""
    _make_problem(tmp_problem_dir, spj=True)
    export_problem(tmp_problem_dir, ["hydrooj"], opts={"dir_name": "20260920_195543"})
    zip_path = tmp_problem_dir / "export" / "hydrooj" / "20260920_195543.zip"
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    root = "20260920_195543"
    for expect in ("problem.json", "problem.yaml", "problem_zh.md",
                   "testdata/config.yaml", "testdata/input.name",
                   "testdata/output.name", "testdata/problem1.in",
                   "testdata/problem1.out", "checker.cpp"):
        assert f"{root}/{expect}" in names, expect

    obj = json.loads(zf.read(f"{root}/problem.json").decode("utf-8"))
    assert obj["title"] == "测试题"
    assert obj["time"] == 2000 and obj["memory"] == 512
    assert obj["difficulty"] == 1500 and obj["tags"] == ["dp"]
    assert obj["file_io"] == {"input": "problem.in", "output": "problem.out"}
    assert [s["input"] for s in obj["samples"]] == ["1\n"]

    cfg = yaml.safe_load(zf.read(f"{root}/testdata/config.yaml").decode("utf-8"))
    assert cfg["time"] == "2000ms" and cfg["memory"] == "512m"
    assert cfg["filename"] == "problem"
    assert cfg["subtasks"][0]["cases"] == [
        {"input": "problem1.in", "output": "problem1.out"},
        {"input": "problem2.in", "output": "problem2.out"},
    ]
    assert zf.read(f"{root}/testdata/input.name").decode("utf-8").strip() == "problem.in"


def test_hydro_alias_matches_hydrooj(tmp_problem_dir, tmp_config):
    _make_problem(tmp_problem_dir)
    a = export_problem(tmp_problem_dir, ["hydro"], opts={"dir_name": "d"})
    b = export_problem(tmp_problem_dir, ["hydrooj"], opts={"dir_name": "d"})
    assert Path(a["hydro"]).name == Path(b["hydrooj"]).name


def test_hydrooj_no_file_io(tmp_problem_dir, tmp_config):
    """--no-file-io：不给 file_io/filename，也不生成 input.name/output.name。"""
    _make_problem(tmp_problem_dir)
    export_problem(tmp_problem_dir, ["hydrooj"],
                   opts={"dir_name": "d", "file_io": False})
    zf = zipfile.ZipFile(tmp_problem_dir / "export" / "hydrooj" / "d.zip")
    names = zf.namelist()
    assert "d/testdata/input.name" not in names
    obj = json.loads(zf.read("d/problem.json").decode("utf-8"))
    assert "file_io" not in obj
    cfg = yaml.safe_load(zf.read("d/testdata/config.yaml").decode("utf-8"))
    assert "filename" not in cfg
    assert "本题的输入文件" not in obj["input"]


def test_hydrooj_parses_statement_sections(tmp_problem_dir, tmp_config):
    """题面按标准小节解析进 problem.json（描述/数据范围/题解/样例解释）。"""
    (tmp_problem_dir / "problem.md").write_text(
        "# 灯笼配对\n\n## 题目描述\n\n数一数。\n\n## 输入格式\n\n一行 $n$。\n\n"
        "## 输出格式\n\n一个整数。\n\n## 样例\n\n### 样例输入 #1\n\n```\n1\n```\n\n"
        "### 样例输出 #1\n\n```\n0\n```\n\n## 样例解释\n\n样例 1：只有一盏。\n\n"
        "## 数据范围\n\n- $1 \\le n \\le 9$\n\n## 题解说明\n\n奇偶性。\n",
        encoding="utf-8")
    (tmp_problem_dir / "solution.cpp").write_text("int main(){}")
    (tmp_problem_dir / "generator.cpp").write_text("int main(){}")
    (tmp_problem_dir / "validator.cpp").write_text("int main(){}")
    (tmp_problem_dir / "naive.cpp").write_text("int main(){}")
    (tmp_problem_dir / "inputs" / "01.in").write_text("1\n")
    (tmp_problem_dir / "outputs" / "01.out").write_text("0\n")
    (tmp_problem_dir / "problem.yaml").write_text(yaml.safe_dump(
        {"title": "灯笼配对", "slug": "lantern", "algorithm_tags": ["数学"],
         "difficulty": 800, "time_limit_ms": 1000, "memory_limit_mb": 256,
         "checker": {"type": "builtin"}}, allow_unicode=True))

    export_problem(tmp_problem_dir, ["hydrooj"], opts={"dir_name": "d"})
    zf = zipfile.ZipFile(tmp_problem_dir / "export" / "hydrooj" / "d.zip")
    obj = json.loads(zf.read("d/problem.json").decode("utf-8"))
    assert obj["title"] == "灯笼配对"
    assert obj["description"] == "数一数。"
    assert obj["input"].startswith("本题的输入文件为 lantern.in")
    assert obj["dataRange"].startswith("- $1 \\le n \\le 9$")
    assert obj["hint"] == "奇偶性。"
    assert obj["samples"][0]["explain"] == "只有一盏。"
    assert obj["samples"][0]["title"] == "样例 1"
    # problem_zh.md 按平台排版重新生成
    zhmd = zf.read("d/problem_zh.md").decode("utf-8")
    assert "### 样例输入 1" in zhmd and "## 数据范围" in zhmd
    assert "样例 1：只有一盏。" in zhmd


def test_polygon_layout(tmp_problem_dir, tmp_config):
    _make_problem(tmp_problem_dir)
    export_problem(tmp_problem_dir, ["polygon"])
    dest = tmp_problem_dir / "export" / "polygon" / tmp_problem_dir.name
    assert (dest / "statements" / "chinese" / "problem.md").exists()
    assert (dest / "solutions" / "main.cpp").exists()
    assert (dest / "tests" / "01").exists()          # input without extension
    assert (dest / "tests" / "01.a").exists()        # answer as NN.a
    assert not (dest / "check.cpp").exists()         # non-SPJ
    assert "wcmp" in (dest / "README_polygon.md").read_text(encoding="utf-8")


def test_export_without_yaml_degrades(tmp_problem_dir, tmp_config, capsys):
    _make_problem(tmp_problem_dir, with_yaml=False)
    export_problem(tmp_problem_dir, ["luogu"])
    assert (tmp_problem_dir / "export" / "luogu" / "data.zip").exists()
    assert "problem.yaml" in capsys.readouterr().out
