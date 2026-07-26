import pipeline
from pipeline import Pipeline


def test_step_compile_missing_sources_fails(tmp_problem_dir):
    pipe = Pipeline(tmp_problem_dir)
    assert pipe._step_compile() is False
    assert any("solution.cpp" in e for e in pipe.errors)
    assert any("naive.cpp" in e for e in pipe.errors)


def test_step_compile_naive_optional_when_skip_stress(tmp_problem_dir, monkeypatch):
    for name in ("generator.cpp", "validator.cpp", "solution.cpp"):
        (tmp_problem_dir / name).write_text("int main(){}")
    monkeypatch.setattr(pipeline, "_compile", lambda src, out: (True, f"Compiled {src.name}"))

    pipe = Pipeline(tmp_problem_dir)
    assert pipe._step_compile(naive_required=True) is False
    pipe2 = Pipeline(tmp_problem_dir)
    assert pipe2._step_compile(naive_required=False) is True
    assert pipe2.errors == []
