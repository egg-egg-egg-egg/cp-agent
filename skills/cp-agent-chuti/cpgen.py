#!/usr/bin/env python3
"""cp-agent-chuti 出题封装脚本。

给 agent 用：注入凭证（从注册表，避免 GUI 弹窗）→ 跑 main.py 出题 → 校验 result.json
与 Hydro 导出包 → 打印摘要。用法见 SKILL.md。

    python cpgen.py --topic dp --difficulty 1500 --name my_problem
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import winreg
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # skills/cp-agent-chuti -> 项目根
VENV = REPO / ".venv" / "Scripts" / "python.exe"

ENV_KEYS = ("DEEPSEEK_API_KEY", "OJ_USER", "OJ_PASSWORD")


def log(msg: str) -> None:
    print(f"[cpgen] {msg}", flush=True)


def read_reg(name: str):
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ)
        v, _ = winreg.QueryValueEx(k, name)
        winreg.CloseKey(k)
        return v
    except FileNotFoundError:
        return None


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ENV_KEYS:
        if not env.get(name):
            v = read_reg(name)
            if v:
                env[name] = v
    if not env.get("DEEPSEEK_API_KEY"):
        log("⚠ 未找到 DEEPSEEK_API_KEY（环境变量或注册表），出题可能因凭证缺失失败")
    return env


def verify(problem_dir: Path, test_count: int) -> bool:
    ok = True
    rj = problem_dir / "result.json"
    if not rj.exists():
        log(f"✗ 无 result.json：{rj}")
        return False
    data = json.loads(rj.read_text(encoding="utf-8"))
    log(f"  success={data.get('success')}  failure_reason={data.get('failure_reason')}")
    log(f"  iterations={data.get('iterations')}  elapsed={data.get('elapsed_sec', 0)/60:.1f}min")
    tok = data.get("tokens", {})
    log(f"  tokens in={tok.get('input')} out={tok.get('output')}")
    art = data.get("artifacts", {})
    if not data.get("success"):
        ok = False
    if art.get("inputs") != test_count or art.get("outputs") != test_count:
        log(f"✗ 测试点数量不符：inputs={art.get('inputs')} outputs={art.get('outputs')} 期望 {test_count}")
        ok = False
    if not art.get("complete"):
        log("✗ artifacts.complete != true")
        ok = False

    zips = sorted((problem_dir / "export" / "hydrooj").glob("*.zip"))
    if zips:
        zp = zips[-1]
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            ins = [n for n in names if n.endswith(".in")]
            outs = [n for n in names if n.endswith(".out")]
        log(f"  Hydro 包: {zp.name} ({zp.stat().st_size/1024:.0f} KB, {len(ins)} in / {len(outs)} out)")
    else:
        log("✗ 未找到 Hydro 导出包")
        ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="cp-agent 出题封装")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--difficulty", type=int, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--test-count", type=int, default=30)
    ap.add_argument("--max-iterations", type=int, default=30)
    ap.add_argument("--export-after", default="hydrooj")
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args()

    repo = args.repo
    if not (repo / "main.py").exists():
        log(f"✗ 项目根不正确：{repo}")
        return 2

    cmd = [str(VENV), "main.py", "--topic", args.topic,
           "--difficulty", str(args.difficulty), "--name", args.name,
           "--test-count", str(args.test_count),
           "--max-iterations", str(args.max_iterations),
           "--export-after", args.export_after]

    log(f"出题: {' '.join(cmd[1:])}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=repo, env=build_env(),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    dur = time.time() - t0
    log(f"main.py exit={r.returncode}  用时 {dur/60:.1f} min")

    tail = (r.stdout or "").strip().splitlines()
    for line in tail[-8:]:
        if line.strip():
            log(f"  | {line}")
    if r.returncode != 0:
        for line in (r.stderr or "").strip().splitlines()[-8:]:
            if line.strip():
                log(f"  | ERR {line}")

    problem_dir = repo / "problems" / args.name
    if not problem_dir.exists():
        log("✗ 题目目录不存在，出题失败")
        return 1

    ok = verify(problem_dir, args.test_count)
    log("结果: " + ("✅ 完整可上传" if ok else "✗ 校验未通过，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
