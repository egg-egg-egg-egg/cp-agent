#!/usr/bin/env python3
"""
tasklist.py —— 出题任务清单工具（CI 与本地共用）

`tasks.yaml` 是出题的唯一数据源：先按它出题，出完回写状态，形成闭环。

命令:
    next [--id ID] [--force] [--format json|github]
                               取下一个待出题任务。`--format github` 直接输出
                               GITHUB_OUTPUT 用的 key=value 行，workflow 里一句
                               `>> "$GITHUB_OUTPUT"` 即可用。
    mark-done ID --name NAME   标记某题为 done 并回填题目目录名
    context TOPIC              输出同 topic 已出题的 focus 列表（供 --extra 注入）
    stats                      打印进度统计

设计说明:
    mark-done 刻意**不用 yaml.dump 回写** —— 那会把 tasks.yaml 里的注释和分组
    排版全部抹掉。改为只对目标行做正则替换，其余字节原样不动。
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
TASKS_PATH = PROJECT_ROOT / "tasks.yaml"


def load_tasks(path: Path = TASKS_PATH) -> list:
    if not path.exists():
        raise SystemExit(f"找不到 {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("problems") or []


def _emit(payload: dict, fmt: str) -> None:
    """按 format 输出：github 模式产出 GITHUB_OUTPUT 的 key=value 行。"""
    if fmt == "github":
        for k, v in payload.items():
            print(f"{k}={v}")
    else:
        print(json.dumps(payload, ensure_ascii=False))


def cmd_next(args) -> int:
    tasks = load_tasks()
    target = None
    fmt = getattr(args, "format", "json")

    if args.id:
        for t in tasks:
            if str(t.get("id")) == str(args.id):
                target = t
                break
        if target is None:
            _emit({"empty": "true", "reason": f"id {args.id} 不存在"}, fmt)
            return 1
        if target.get("status") == "done" and not args.force:
            _emit({"empty": "true", "reason": f"id {args.id} 已是 done"}, fmt)
            return 1
    else:
        for t in tasks:
            if t.get("status") == "pending":
                target = t
                break
        if target is None:
            _emit({"empty": "true", "reason": "没有 pending 任务"}, fmt)
            return 1

    _emit({
        "empty": "false",
        "id": str(target.get("id")),
        "topic": target.get("topic", ""),
        "focus": target.get("focus", ""),
        "difficulty": str(target.get("difficulty", "")),
        "name": target.get("name", "") or "",
    }, fmt)
    return 0


def cmd_mark_done(args) -> int:
    tasks = load_tasks()
    if not any(str(t.get("id")) == str(args.id) for t in tasks):
        raise SystemExit(f"id {args.id} 不存在")
    if not args.name:
        raise SystemExit("--name 不能为空")

    lines = TASKS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    hit = False
    out = []
    for ln in lines:
        if re.search(r'\bid:\s*"%s"' % re.escape(str(args.id)), ln):
            new = re.sub(r'(\bstatus:\s*)\w+', r'\1done', ln, count=1)
            new = re.sub(r'(\bname:\s*)"[^"]*"',
                         lambda m: m.group(1) + '"%s"' % args.name, new, count=1)
            hit = True
            out.append(new)
        else:
            out.append(ln)
    if not hit:
        raise SystemExit(f"没能定位 id {args.id} 所在行（格式被改过？）")

    TASKS_PATH.write_text("".join(out), encoding="utf-8")
    print(f"tasks.yaml: id {args.id} -> done, name={args.name}")
    return 0


def cmd_context(args) -> int:
    tasks = load_tasks()
    rows = [t for t in tasks
            if t.get("status") == "done" and t.get("topic") == args.topic]
    for t in rows:
        focus = t.get("focus", "")
        name = t.get("name", "") or ""
        print(f"- {focus}" + (f"（{name}）" if name else ""))
    return 0


def cmd_stats(args) -> int:
    tasks = load_tasks()
    st = collections.Counter(t.get("status") for t in tasks)
    print(f"总计 {len(tasks)} | " + " ".join(f"{k}={v}" for k, v in st.items()))
    pend = collections.Counter(t.get("topic") for t in tasks
                               if t.get("status") == "pending")
    if pend:
        print("待出 topic 分布: "
              + " ".join(f"{k}={v}" for k, v in pend.most_common()))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="tasks.yaml 出题任务清单工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("next", help="取下一个待出题任务")
    n.add_argument("--id", default=None, help="指定题目 id；默认取首个 pending")
    n.add_argument("--force", action="store_true", help="即使已是 done 也返回")
    n.add_argument("--format", choices=["json", "github"], default="json",
                   help="json（默认，本地用）或 github（GITHUB_OUTPUT 的 key=value）")
    n.set_defaults(func=cmd_next)

    m = sub.add_parser("mark-done", help="标记为已完成并回填题目目录名")
    m.add_argument("id")
    m.add_argument("--name", required=True, help="题目目录名")
    m.set_defaults(func=cmd_mark_done)

    c = sub.add_parser("context", help="输出同 topic 已出题清单")
    c.add_argument("topic")
    c.set_defaults(func=cmd_context)

    s = sub.add_parser("stats", help="打印进度统计")
    s.set_defaults(func=cmd_stats)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
