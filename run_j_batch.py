#!/usr/bin/env python3
"""串行触发 GitHub Actions 逐题出题（CSP-J T1/T2 专项 30 题）。

为什么串行：`generate.yml` 每次跑完会 commit tasks.yaml（mark-done）。并发跑多个
job 会同时改同一文件 —— 虽然 workflow 里有 pull --rebase 重试，但同 topic 的题改的
是同一行，冲突概率高。串行最稳。

断点续传：每次循环先读 tasks.yaml，跳过已是 done 的题；中断后重跑即接着往下。

用法：
  python run_j_batch.py            # 跑 tasks.yaml 里所有 pending 的 J 题
  python run_j_batch.py J01 J02    # 只跑指定几题
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

PROJECT_ROOT = pathlib.Path(__file__).parent
TASKS = PROJECT_ROOT / "tasks.yaml"
WORKFLOW = "Generate Problem"
PROVIDER = "openai.deepseek"
MAX_TOKENS = "65536"


def gh_env():
    """gh 需要有效 token：keyring 里的会 401，改从 git credential helper 取。"""
    env = dict(os.environ)
    if env.get("GH_TOKEN"):
        return env
    try:
        p = subprocess.run(
            ["git", "credential", "fill"], cwd=str(PROJECT_ROOT),
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30,
        )
        for ln in p.stdout.splitlines():
            if ln.startswith("password="):
                env["GH_TOKEN"] = ln.split("=", 1)[1]
    except Exception as e:  # noqa: BLE001
        print("取 token 失败: %r" % e, flush=True)
    return env


def load_pending(only=None):
    ps = yaml.safe_load(TASKS.read_text(encoding="utf-8"))["problems"]
    out = []
    for p in ps:
        if not str(p.get("id", "")).startswith("J"):
            continue
        if p.get("status") != "pending":
            continue
        if only and p["id"] not in only:
            continue
        out.append(p["id"])
    return out


def run_one(task_id, env):
    """触发一题并等它跑完。返回 (成功?, run_id)。"""
    subprocess.run(
        ["gh", "workflow", "run", WORKFLOW, "--ref", "main",
         "-f", "task_id=%s" % task_id, "-f", "provider=%s" % PROVIDER,
         "-f", "max_tokens=%s" % MAX_TOKENS],
        cwd=str(PROJECT_ROOT), env=env, check=True, timeout=120,
    )
    time.sleep(8)  # 等 run 被创建
    p = subprocess.run(
        ["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "1",
         "--json", "databaseId,status"],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    rid = json.loads(p.stdout)[0]["databaseId"]
    print("  → run %s 已启动，等待完成..." % rid, flush=True)
    w = subprocess.run(
        ["gh", "run", "watch", str(rid), "--exit-status", "--interval", "30"],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=1800,
    )
    return w.returncode == 0, rid


def main():
    only = [a for a in sys.argv[1:] if a.startswith("J")]
    env = gh_env()
    if not env.get("GH_TOKEN"):
        print("✗ 无 GH_TOKEN，中止", flush=True)
        return 1

    pending = load_pending(only or None)
    print("待出 J 题 %d 道: %s" % (len(pending), ", ".join(pending)), flush=True)

    ok = fail = 0
    for i, tid in enumerate(pending, 1):
        print("\n[%d/%d] %s" % (i, len(pending), tid), flush=True)
        try:
            good, rid = run_one(tid, env)
            if good:
                ok += 1
                print("  ✓ %s 完成（run %s）" % (tid, rid), flush=True)
            else:
                fail += 1
                print("  ✗ %s 失败（run %s），继续下一题" % (tid, rid), flush=True)
        except subprocess.TimeoutExpired:
            fail += 1
            print("  ✗ %s 超时（>30min），继续下一题" % tid, flush=True)
        except Exception as e:  # noqa: BLE001
            fail += 1
            print("  ✗ %s 异常: %r，继续下一题" % (tid, e), flush=True)
        # 同步 CI 回写的 tasks.yaml（避免下次触发时本地落后导致 pull 冲突）
        subprocess.run(["git", "pull", "--rebase", "origin", "main"],
                       cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120)

    print("\n=== 汇总：成功 %d / 失败 %d ===" % (ok, fail), flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
