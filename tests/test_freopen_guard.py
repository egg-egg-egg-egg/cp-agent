"""solution/naive 里出现 freopen 必须被 compile 硬拦。

背景：本项目有两套运行环境，对 freopen 要求相反 ——
  · CI/pipeline 用 `solution < input.in`（stdin 重定向）跑代码 ⇒ 标程必须 stdin/stdout；
  · OJ 是 file_io 模式 ⇒ 必须 freopen（否则 RE）。
解法是标程保持纯净，提交 OJ 时由 batch_submit.py 的 fio_base() 自动注入 freopen。
所以 solution.cpp / naive.cpp 一旦手写 freopen，就是错的，必须在编译期拦下。
"""
import pipeline
from pipeline import tool_compile_cpp


def test_solution_with_freopen_rejected(tmp_problem_dir):
    (tmp_problem_dir / "solution.cpp").write_text(
        '#include <cstdio>\nint main(){ freopen("a.in","r",stdin); return 0; }',
        encoding="utf-8",
    )
    r = tool_compile_cpp(tmp_problem_dir, "solution.cpp", "bin/solution")
    assert r["success"] is False
    assert r.get("freopen") is True
    assert "freopen" in r["message"]


def test_naive_with_freopen_rejected(tmp_problem_dir):
    (tmp_problem_dir / "naive.cpp").write_text(
        '#include <cstdio>\nint main(){ freopen("a.out","w",stdout); return 0; }',
        encoding="utf-8",
    )
    r = tool_compile_cpp(tmp_problem_dir, "naive.cpp", "bin/naive")
    assert r["success"] is False
    assert r.get("freopen") is True


def test_solution_without_freopen_compiles(tmp_problem_dir, monkeypatch):
    (tmp_problem_dir / "solution.cpp").write_text("int main(){ return 0; }", encoding="utf-8")
    monkeypatch.setattr(pipeline, "_compile", lambda src, out: (True, "ok"))
    r = tool_compile_cpp(tmp_problem_dir, "solution.cpp", "bin/solution")
    assert r["success"] is True
    assert r.get("freopen") is None


def test_generator_may_use_freopen(tmp_problem_dir, monkeypatch):
    """generator 不受此限制（它本来就靠 argv 写文件），只拦 solution/naive。"""
    (tmp_problem_dir / "generator.cpp").write_text(
        '#include <cstdio>\nint main(){ freopen("x.in","w",stdout); return 0; }',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_compile", lambda src, out: (True, "ok"))
    r = tool_compile_cpp(tmp_problem_dir, "generator.cpp", "bin/generator")
    assert r["success"] is True
