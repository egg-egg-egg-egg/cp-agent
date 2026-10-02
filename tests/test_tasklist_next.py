"""tasklist.py `next` 的**空串 topic** 必须按「不筛选」处理。

背景（2026-10-02 实踩）：generate.yml 里是
    python tasklist.py next --id "$IN_TASK_ID" --topic "$IN_TOPIC_FILTER" --format github
用户没指定时，这两个变量展开成**空字符串**而不是 None。
旧逻辑写的是 `args.topic is None or t.get("topic") == args.topic`，
空串让条件退化成「topic 必须等于空串」⇒ 过滤掉所有题、返回「没有 pending 任务」，
而 tasks.yaml 里明明 pending 一堆。症状是 CI 每次 16 秒空转结束（Generate 从没执行）。

这个 bug 隐蔽在于：**只要指定了 task_id 就绕过**（走 --id 分支不看 topic），
所以「单题触发」一直正常，只有「批量/不带 id 触发」才爆。
"""
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _next(*args):
    p = subprocess.run(
        [sys.executable, str(ROOT / "tasklist.py"), "next", *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)


def _has_pending():
    ps = yaml.safe_load((ROOT / "tasks.yaml").read_text(encoding="utf-8"))["problems"]
    return any(p.get("status") == "pending" for p in ps)


@pytest.mark.skipif(not _has_pending(), reason="tasks.yaml 无 pending，跳过")
def test_empty_id_and_topic_still_finds_task():
    """CI 的真实调用形态：--id "" --topic "" 必须仍能取到题。"""
    r = _next("--id", "", "--topic", "", "--format", "github")
    assert r.get("empty") == "false", (
        "空串 topic 被当成有效筛选条件了 —— 所有题都会被过滤掉（这是 2026-10-02 的 bug）"
    )
    assert r.get("id")


@pytest.mark.skipif(not _has_pending(), reason="tasks.yaml 无 pending，跳过")
def test_no_args_still_finds_task():
    r = _next("--format", "github")
    assert r.get("empty") == "false"
    assert r.get("id")


def test_topic_filter_still_works():
    """正常筛选不能被破坏：给的 topic 不存在时必须返回 empty。"""
    r = _next("--topic", "__definitely_not_a_topic__", "--format", "github")
    assert r.get("empty") == "true"
