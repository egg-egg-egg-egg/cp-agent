import sys

import pytest

from pipeline import _child_mem_mb, _run_cmd


def test_run_cmd_with_mem_limit_normal_command():
    """有 mem_mb 时正常命令不受影响（各平台）。"""
    code, out, _ = _run_cmd([sys.executable, "-c", "print('ok')"], mem_mb=512)
    assert code == 0
    assert out.strip() == "ok"


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS 仅在 Linux 上可靠强制")
def test_run_cmd_mem_limit_kills_hog():
    code, _, _ = _run_cmd(
        [sys.executable, "-c", "x = bytearray(400 * 1024 * 1024)"],
        mem_mb=100, timeout=20,
    )
    assert code != 0


def test_child_mem_mb_from_problem_yaml(tmp_problem_dir, tmp_config):
    (tmp_problem_dir / "problem.yaml").write_text("memory_limit_mb: 128\n")
    assert _child_mem_mb(tmp_problem_dir) == 512      # ×4 安全网


def test_child_mem_mb_default(tmp_problem_dir, tmp_config):
    assert _child_mem_mb(tmp_problem_dir) == 256 * 4  # config 默认 256MB
