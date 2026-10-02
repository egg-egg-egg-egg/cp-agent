#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等 CI 出完题 → 自动走完后半程全流程（下载 → 上传 → 提交标程 → 看板核验）。

背景：run_j_batch.py 用 nohup 也会在会话结束时被杀（WorkBuddy 清主 agent 的进程树）。
本脚本用 DETACHED_PROCESS 启动（见 launch_detached.py），真正脱离，可跨会话存活。

流程（对应黄sir 2026-10-02 确认的 5 步）：
  ② download_problems.py   下载产物 → 门禁 → 落 problems/ → 自动同步看板
  ③ batch_upload.py        上传 OJ（写 uploaded_pids.txt）→ 自动同步看板
  ④ batch_submit.py        提交标程（自动注入 freopen，串行 15s）
  ⑤ sync_dashboard.py      看板核验（每次自动同步已覆盖，这里再核一遍）

轮询策略：每 5 分钟扫一次 tasks.yaml 的 J 题；待 pending 归零即进入后半程。
超时保护：最多等 MAX_WAIT_HOURS 小时，超时也照样走后半程（把已出的题先处理掉）。

用法:
  python auto_pipeline.py           # 直接跑（前台，调试用）
  python launch_detached.py         # 脱离启动（正式用法，跨会话存活）
日志: auto_pipeline.log
"""
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime

import yaml

ROOT = pathlib.Path(__file__).parent
TASKS = ROOT / "tasks.yaml"
LOG = ROOT / "auto_pipeline.log"
PY = sys.executable

POLL_INTERVAL = 300       # 轮询间隔（秒）
MAX_WAIT_HOURS = 14       # 最长等待出题时间（小时）


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def run(cmd, timeout=3600):
    """跑一个子命令，日志实时落盘。返回 (returncode, 输出尾部)。"""
    log("  $ %s" % " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        tail = (p.stdout or "")[-1500:]
        if p.returncode != 0:
            log("  ! 退出码 %d\n%s" % (p.returncode, tail[-800:]))
        return p.returncode, tail
    except subprocess.TimeoutExpired:
        log("  ! 超时（>%ds）" % timeout)
        return -1, ""


def j_status():
    """返回 (pending 数, done 数)。读 tasks.yaml。"""
    try:
        ps = yaml.safe_load(TASKS.read_text(encoding="utf-8"))["problems"]
    except Exception as e:  # noqa: BLE001
        log("  读 tasks.yaml 失败: %r" % e)
        return -1, -1
    js = [p for p in ps if str(p.get("id", "")).startswith("J")]
    pend = sum(1 for p in js if p.get("status") == "pending")
    done = sum(1 for p in js if p.get("status") == "done")
    return pend, done


def git_pull():
    run(["git", "pull", "--rebase", "origin", "main"], timeout=180)


def wait_for_generation():
    """轮询直到 J 题 pending 归零（或超时）。"""
    start = time.time()
    deadline = start + MAX_WAIT_HOURS * 3600
    while True:
        git_pull()  # CI 在写 tasks.yaml，先同步
        pend, done = j_status()
        log("出题进度：J done=%d / pending=%d" % (done, pend))
        if pend == 0:
            log("✓ 出题完成（pending 已归零）")
            return True
        if time.time() > deadline:
            log("⚠ 等待超时（%d 小时），继续走后半程处理已出的题" % MAX_WAIT_HOURS)
            return False
        time.sleep(POLL_INTERVAL)


def main():
    log("=" * 60)
    log("auto_pipeline 启动（等出题 → 下载 → 上传 → 提交 → 核验）")

    wait_for_generation()
    git_pull()

    # ② 下载产物（内部自动同步看板）
    log("── ② 下载产物 + 门禁 ──")
    rc, out = run([PY, "download_problems.py"], timeout=3600)
    if rc != 0:
        log("  下载异常，仍继续（可能部分成功）")

    # ③ 上传 OJ（尾部自动同步看板）
    log("── ③ 上传 OJ ──")
    rc, out = run([PY, "batch_upload.py"], timeout=5400)
    if rc != 0:
        log("  上传异常，仍继续")

    # ④ 提交标程（串行 15s，题多会慢）
    log("── ④ 提交标程 ──")
    rc, out = run([PY, "batch_submit.py"], timeout=10800)

    # ⑤ 看板核验
    log("── ⑤ 看板核验（dry-run）──")
    run([PY, "sync_dashboard.py", "--dry-run"], timeout=600)

    pend, done = j_status()
    log("=" * 60)
    log("全流程结束：J done=%d / pending=%d" % (done, pend))
    log("详见上一步各命令输出；出题失败的题仍是 pending，可重跑本脚本续做")
    return 0


if __name__ == "__main__":
    sys.exit(main())
