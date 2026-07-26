import os

from pipeline import _sandbox_resolve


def test_absolute_path_rejected(tmp_problem_dir):
    _, err = _sandbox_resolve(tmp_problem_dir, "/etc/passwd")
    assert "绝对路径" in err


def test_dotdot_rejected(tmp_problem_dir):
    _, err = _sandbox_resolve(tmp_problem_dir, "../outside.txt")
    assert ".." in err
    _, err = _sandbox_resolve(tmp_problem_dir, "a/../../outside.txt")
    assert ".." in err


def test_sibling_prefix_dir_rejected(tmp_path):
    """base_evil/ must not pass a sandbox rooted at base/ (startswith bug)."""
    base = tmp_path / "base"
    evil = tmp_path / "base_evil"
    base.mkdir()
    evil.mkdir()
    os.symlink(evil, base / "link")
    _, err = _sandbox_resolve(base, "link/f.txt")
    assert "越界" in err


def test_symlink_escape_rejected(tmp_problem_dir, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    os.symlink(outside, tmp_problem_dir / "alias.txt")
    _, err = _sandbox_resolve(tmp_problem_dir, "alias.txt")
    assert "越界" in err


def test_normal_nested_path_ok(tmp_problem_dir):
    resolved, err = _sandbox_resolve(tmp_problem_dir, "bin/solution")
    assert err == ""
    assert resolved == tmp_problem_dir.resolve() / "bin" / "solution"


def test_problem_dir_itself_ok(tmp_problem_dir):
    resolved, err = _sandbox_resolve(tmp_problem_dir, ".")
    assert err == ""
    assert resolved == tmp_problem_dir.resolve()
