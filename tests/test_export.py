import zipfile

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


def test_hydro_package(tmp_problem_dir, tmp_config):
    _make_problem(tmp_problem_dir, spj=True)
    export_problem(tmp_problem_dir, ["hydro"])
    slug = tmp_problem_dir.name
    zip_path = tmp_problem_dir / "export" / "hydro" / f"{slug}.zip"
    names = zipfile.ZipFile(zip_path).namelist()
    assert f"{slug}/problem.yaml" in names
    assert f"{slug}/problem_zh.md" in names
    assert f"{slug}/testdata/01.in" in names
    assert f"{slug}/testdata/config.yaml" in names
    assert f"{slug}/testdata/checker.cc" in names
    cfg = yaml.safe_load(
        zipfile.ZipFile(zip_path).read(f"{slug}/testdata/config.yaml"))
    assert cfg == {"time": "2000ms", "memory": "512m",
                   "checker_type": "testlib", "checker": "checker.cc"}


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
