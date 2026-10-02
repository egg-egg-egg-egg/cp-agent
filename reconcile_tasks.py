#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对账：并行出题时 `mark-done` 回写失败的题，从 CI 日志补回 tasks.yaml。

背景（2026-10-02）：并行触发多个出题 job 时，它们会同时 push tasks.yaml。
旧逻辑用 `git pull --rebase` 重试 —— 但 tasks.yaml 的题目是**相邻行**，改 J23 会让
J24 的 diff 上下文变化 ⇒ rebase 必然冲突 ⇒ 回写失败（job 标记 failure）。
**但题目本身是成功的**（Generate 步骤 success、artifact 已上传）。

所以 job 的 failure ≠ 出题失败。本脚本从 CI 日志里捞出
    IN_TASK_ID: J23
    name=binary_search_700_225
配对后补写 tasks.yaml（幂等，已 done 的跳过）。

用法:
  python reconcile_tasks.py --list      # 只看差异，不改
  python reconcile_tasks.py             # 补写 tasks.yaml
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
TASKS = ROOT / "tasks.yaml"
WORKFLOW = "Generate Problem"


def gh_env():
    env = dict(os.environ)
    if env.get("GH_TOKEN"):
        return env
    p = subprocess.run(["git", "credential", "fill"], cwd=str(ROOT),
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, timeout=30)
    for ln in p.stdout.splitlines():
        if ln.startswith("password="):
            env["GH_TOKEN"] = ln.split("=", 1)[1]
    return env


def gh(args, env, timeout=180):
    p = subprocess.run(["gh"] + args, cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=timeout)
    return p.stdout


def recent_runs(env, limit=40):
    out = gh(["run", "list", "--workflow", WORKFLOW, "--limit", str(limit),
              "--json", "databaseId,conclusion"], env)
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return []


def has_artifact(env, run_id):
    out = gh(["api", f"repos/egg-egg-egg-egg/cp-agent/actions/runs/{run_id}/artifacts",
              "-q", f'.artifacts[] | select(.name=="problem-{run_id}") | .name'], env)
    return bool(out.strip())


def extract_from_log(env, run_id):
    """从 job 日志里捞 (task_id, problem_name)。返回 None 表示没捞到。"""
    log = gh(["run", "view", str(run_id), "--log"], env, timeout=300)
    m_id = re.search(r"IN_TASK_ID:\s*(J\d+)", log)
    m_name = re.search(r"name=([a-z_0-9]+_\d+_\d+)", log)
    if m_id and m_name:
        return m_id.group(1), m_name.group(1)
    return None


def load_tasks():
    return yaml.safe_load(TASKS.read_text(encoding="utf-8"))["problems"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只报告不改")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    env = gh_env()
    if not env.get("GH_TOKEN"):
        print("✗ 无 GH_TOKEN")
        return 1

    tasks = load_tasks()
    by_id = {str(t["id"]): t for t in tasks}

    print(f"扫描最近 {args.limit} 个 run...")
    missing = []
    for r in recent_runs(env, args.limit):
        rid = r["databaseId"]
        if not has_artifact(env, rid):
            continue
        got = extract_from_log(env, rid)
        if not got:
            continue
        tid, name = got
        t = by_id.get(tid)
        if t is None:
            continue
        # 该题在 tasks.yaml 里还没登记名字 ⇒ 需要补
        if t.get("status") != "done" or not t.get("name"):
            missing.append((tid, name, rid))

    if not missing:
        print("✓ 无差异，tasks.yaml 已是最新")
        return 0

    print(f"\n需补 {len(missing)} 条：")
    for tid, name, rid in missing:
        print(f"  {tid}  {name}   (run {rid})")

    if args.list:
        print("\n（--list，未改动）")
        return 0

    # 逐条补写（每次基于最新 tasks.yaml，避免互相覆盖）
    ok = 0
    for tid, name, rid in missing:
        for _ in range(5):
            subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=str(ROOT))
            subprocess.run(["git", "reset", "--hard", "-q", "origin/main"], cwd=str(ROOT))
            subprocess.run([sys.executable, "tasklist.py", "mark-done", tid, "--name", name],
                           cwd=str(ROOT), capture_output=True, text=True)
            subprocess.run(["git", "add", "tasks.yaml"], cwd=str(ROOT))
            c = subprocess.run(["git", "commit", "-q", "-m",
                                f"chore(tasks): #{tid} 出题完成（{name}）[对账补录]"],
                               cwd=str(ROOT), capture_output=True, text=True)
            if c.returncode != 0:   # 无变化
                ok += 1
                break
            if subprocess.run(["git", "push", "-q", "origin", "main"],
                              cwd=str(ROOT)).returncode == 0:
                ok += 1
                break
        else:
            print(f"  ✗ {tid} 补写失败（重试 5 次）")

    print(f"\n完成：补录 {ok}/{len(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
