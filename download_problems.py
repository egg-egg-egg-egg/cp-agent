#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 CI 出题产物 → 静态门禁质检 → 落到 problems/ → 自动同步看板。

一条命令跑完「下载 → 质检 → 入库 → 同步」全链路：

  1. 取 GitHub Actions 最近「Generate Problem」成功运行的 artifact（problem-<run_id>）
  2. 跳过已处理过的 run（记在 .dashboard/downloaded_runs.txt），避免重复下载
  3. 下载 → 读 result.json 取 problem_name → 把临时目录改成该名字
  4. 静态门禁质检（problem_gate.py）：
       通过 → 落到 problems/<name>/
       失败 → 挪到 .dashboard/rejected/<run>__<name>/ 并列出原因
              （既不进 problems/，更不会进看板）
  5. 调用 sync_dashboard.py：门禁 + 新增同步 + PID 回填

用法:
  python download_problems.py --list           # 只列出候选 artifact（不下载）
  python download_problems.py                  # 下载所有未处理的
  python download_problems.py --run 36315377762
  python download_problems.py --no-sync        # 下完不自动同步
  python download_problems.py --limit 30       # 扫描最近多少条 run（默认 20）

token 来源（依次）：GH_TOKEN / GITHUB_TOKEN 环境变量 → gh auth token → 本机 git 凭证
helper（~/.git-credential-github.sh）。全程不打印 token。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_SCRIPTS = os.path.join(HERE, ".workbuddy", "skills", "problem-dashboard", "scripts")
sys.path.insert(0, SKILL_SCRIPTS)
try:
    import problem_gate
except ImportError:
    problem_gate = None

REPO = "egg-egg-egg-egg/cp-agent"
WORKFLOW = "generate.yml"
STATE_DIR = os.path.join(HERE, ".dashboard")
RUNS_FILE = os.path.join(STATE_DIR, "downloaded_runs.txt")
REJECT_DIR = os.path.join(STATE_DIR, "rejected")
PROBLEMS = os.path.join(HERE, "problems")


# ─────────────────────────── token / gh ───────────────────────────

def _probe(token):
    """验证 token 真的能用（有些来源会给出已失效的旧 token）。"""
    if not token:
        return False
    r = gh(["api", "user", "--jq", ".login"], token, timeout=30)
    return r.returncode == 0 and bool(r.stdout.strip())


def get_token():
    """按优先级逐个取 token 并**验证**，返回 (token, 来源)；都失败返回 ('', '')。"""
    cands = []
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            cands.append((os.environ[k].strip(), "环境变量 %s" % k))
    gh_exe = shutil.which("gh")
    if gh_exe:
        r = subprocess.run([gh_exe, "auth", "token"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            cands.append((r.stdout.strip(), "gh auth token"))
    helper = os.path.join(os.path.expanduser("~"), ".git-credential-github.sh")
    sh = shutil.which("sh")
    if sh and os.path.exists(helper):
        r = subprocess.run([sh, helper, "get"], input="protocol=https\nhost=github.com\n\n",
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith("password="):
                cands.append((line[len("password="):].strip(), "git 凭证 helper"))
    tried = []
    for tok, src in cands:
        if _probe(tok):
            return tok, src
        tried.append(src)
    if tried:
        sys.stderr.write("[!] 以下来源的 token 均失效：%s\n" % ", ".join(tried))
    return "", ""


def gh(args, token, timeout=2400):
    exe = shutil.which("gh") or "gh"
    env = dict(os.environ, GH_TOKEN=token)
    return subprocess.run([exe] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def list_success_runs(token, limit):
    r = gh(["run", "list", "--workflow", WORKFLOW, "--repo", REPO,
            "--limit", str(limit), "--status", "completed",
            "--json", "databaseId,conclusion,createdAt,displayTitle"], token)
    if r.returncode != 0:
        raise RuntimeError("gh run list 失败：%s" % (r.stderr or r.stdout)[:400])
    # ⚠️ 不能只认 success：并行出题时 job 会因 `mark-done` 回写 tasks.yaml 失败
    # （相邻行 rebase 冲突）而被标记为 failure，但 **Generate 步骤是成功的、artifact
    # 也在**（题目完整）。这类 run 必须一并下载，否则漏题（2026-10-02 实测漏 15 题）。
    # 真正的判据是「有没有 artifact」，下游下载失败会自动跳过。
    return [x for x in json.loads(r.stdout or "[]")
            if x.get("conclusion") in ("success", "failure")]


# ─────────────────────────── 状态 ───────────────────────────

def load_processed():
    if not os.path.exists(RUNS_FILE):
        return set()
    return {line.strip() for line in open(RUNS_FILE, encoding="utf-8") if line.strip()}


def mark_processed(rid):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RUNS_FILE, "a", encoding="utf-8") as f:
        f.write("%s\n" % rid)


def read_name(raw_dir):
    """从产物里取题目目录名：优先 result.json.problem_name，回退 problem.yaml.slug。"""
    rj = os.path.join(raw_dir, "result.json")
    if os.path.exists(rj):
        try:
            n = str((json.load(open(rj, encoding="utf-8")) or {}).get("problem_name") or "").strip()
            if n:
                return n
        except Exception:
            pass
    if problem_gate:
        meta = problem_gate._meta(raw_dir) or {}
        return str(meta.get("slug") or "").strip()
    return ""


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="下载 CI 出题产物并同步看板")
    ap.add_argument("--list", action="store_true", help="只列出候选 artifact")
    ap.add_argument("--run", default="", help="只处理指定 run id")
    ap.add_argument("--limit", type=int, default=20, help="扫描最近多少条 run")
    ap.add_argument("--no-sync", dest="no_sync", action="store_true", help="下完不自动同步")
    ap.add_argument("--strict", action="store_true", help="同步时门禁告警也当失败")
    a = ap.parse_args()

    token, src = get_token()
    if not token:
        print("[!] 取不到 GitHub token。请设置 GH_TOKEN，或运行 gh auth login。", file=sys.stderr)
        return 2
    print("GitHub token 来源：%s" % src, file=sys.stderr)

    processed = load_processed()
    try:
        runs = list_success_runs(token, a.limit)
    except RuntimeError as e:
        print("[!] %s" % e, file=sys.stderr)
        return 1

    if a.run:
        runs = [r for r in runs if str(r["databaseId"]) == str(a.run)]
        if not runs:
            print("[!] 最近的 run 里没有 %s（可用 --limit 扩大范围）" % a.run, file=sys.stderr)
            return 1

    if a.list:
        print("%-12s %-20s %s" % ("run_id", "createdAt", "状态"))
        for r in runs:
            rid = str(r["databaseId"])
            print("%-12s %-20s %s" % (rid, r.get("createdAt", ""),
                                      "已处理" if rid in processed else "待下载"))
        return 0

    placed, rejected, skipped = [], [], []
    for r in runs:
        rid = str(r["databaseId"])
        if rid in processed:
            continue
        print("\n▸ run %s (%s)" % (rid, r.get("createdAt", "")), flush=True)
        tmp = tempfile.mkdtemp(prefix="dl_%s_" % rid)
        raw = os.path.join(tmp, "raw")
        try:
            dr = gh(["run", "download", rid, "-R", REPO, "-n", "problem-%s" % rid, "-D", raw], token)
            if dr.returncode != 0:
                print("  无 artifact 或下载失败，跳过（%s）" % (dr.stderr or "").strip()[:120])
                mark_processed(rid)          # 别反复重试
                continue

            name = read_name(raw)
            if not name:
                print("  ✘ 读不到 problem_name，跳过")
                mark_processed(rid)
                continue

            named = os.path.join(tmp, name)
            os.rename(raw, named)
            rep = problem_gate.check(named, expect_name=name) if problem_gate else {"ok": True, "fail": [], "warn": []}

            if rep["ok"]:
                dest = os.path.join(PROBLEMS, name)
                if os.path.exists(dest):
                    print("  = problems/%s 已存在，跳过" % name)
                    skipped.append(name)
                else:
                    shutil.move(named, dest)
                    print("  ✓ 已落地 problems/%s" % name)
                    placed.append(name)
                for w in rep.get("warn", []):
                    print("    [告警] %s" % w)
            else:
                os.makedirs(REJECT_DIR, exist_ok=True)
                dest = os.path.join(REJECT_DIR, "%s__%s" % (rid, name))
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.move(named, dest)
                print("  ✘ 门禁拦下 → 挪到 .dashboard/rejected/%s__%s" % (rid, name))
                for f in rep["fail"]:
                    print("      ✘ %s" % f)
                rejected.append((name, rep["fail"]))
            mark_processed(rid)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n下载结果：落地 %d，已存在 %d，被门禁拦下 %d" % (len(placed), len(skipped), len(rejected)))

    if a.no_sync:
        print("（--no-sync，跳过看板同步）")
        return 0

    print("\n── 同步看板 ──")
    cmd = [sys.executable, os.path.join(HERE, "sync_dashboard.py")]
    if a.strict:
        cmd.append("--strict")
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
