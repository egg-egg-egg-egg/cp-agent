#!/usr/bin/env python3
"""Secret scanner for git pre-commit.

阻断命中凭证特征的提交。设计原则：

1. 宁可误报也不漏报 —— 命中后明确告诉用户如何判读与放行。
2. 允许占位符 —— *.example 与 tests/** 中的空值/占位值不阻断。
3. 可绕过 —— 确认真是误报时用 `git commit --no-verify`，并在 commit 信息里说明。

也可独立运行扫描工作区：
    python .githooks/scan_secrets.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 占位符 / 假值特征：命中这些子串的值不视为真凭证
PLACEHOLDER_MARKERS = (
    "xxx", "your_", "change_me", "changeme", "example", "dummy",
    "placeholder", "fake", "mock", "test_key", "dead", "todo",
)

# 视觉上显然的占位：值由重复字符构成（如 0000...、sk-aaaa）
REPEAT_RE = re.compile(r"^(.)\1{7,}$")

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI/DeepSeek 风格 key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("私钥 PEM", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password 明文赋值", re.compile(
        r"(?i)\bpassword\s*[=:]\s*[\"']([^\"'\s]{4,})[\"']")),
    ("api_key 明文赋值", re.compile(
        r"(?i)\bapi[_-]?key\s*[=:]\s*[\"']([^\"'\s]{8,})[\"']")),
    ("secret/token 明文赋值", re.compile(
        r"(?i)\b(?:secret|access[_-]?token|auth[_-]?token)\s*[=:]\s*"
        r"[\"']([^\"'\s]{8,})[\"']")),
]

# 这些路径下的命中宽容处理（仍会报告，但倾向放行）
LENIENT_PATHS = (".example", "/tests/", "test_", "/fixtures/")


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout or ""


def is_placeholder(value: str) -> bool:
    low = value.lower()
    if any(m in low for m in PLACEHOLDER_MARKERS):
        return True
    body = re.sub(r"^(sk-|Bearer )", "", value)
    return bool(REPEAT_RE.match(body))


def is_lenient(path: str) -> bool:
    return any(p in path for p in LENIENT_PATHS)


def scan_text(path: str, text: str) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for label, pat in PATTERNS:
        for m in pat.finditer(text):
            value = m.group(1) if m.groups() else m.group(0)
            if is_placeholder(value):
                continue
            line = text[: m.start()].count("\n") + 1
            hits.append((path, line, label, value[:40]))
    return hits


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()

    staged = [f for f in git("diff", "--cached", "--name-only",
                             "--diff-filter=ACM").splitlines() if f.strip()]
    if not staged:
        return 0

    root = Path(git("rev-parse", "--show-toplevel").strip())
    hard_hits: list[tuple[str, int, str, str]] = []
    soft_hits: list[tuple[str, int, str, str]] = []

    for rel in staged:
        p = root / rel
        if not p.is_file() or p.stat().st_size > 3_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for h in scan_text(rel, text):
            (soft_hits if is_lenient(rel) else hard_hits).append(h)

    if not hard_hits:
        if soft_hits:
            print("注意：以下位置疑似凭证但位于示例/测试，已放行：")
            for path, line, label, val in soft_hits:
                print(f"  {path}:{line}  {label}  -> {val}")
        return 0

    print("=" * 62)
    print("⛔ 提交被拦截：暂存区疑似包含真实凭证")
    print("=" * 62)
    for path, line, label, val in hard_hits:
        print(f"  {path}:{line}  [{label}]  -> {val}")
    print("\n处理建议：")
    print("  1. 把凭证移出仓库，改用环境变量（本项目已支持 env_key / OJ_* 环境变量）")
    print("  2. 确认并提交已在 .gitignore 中的文件 —— 请先 git rm --cached <file>")
    print("  3. 若确认是误报：git commit --no-verify，并在 commit 信息里写明原因")
    return 1


def self_test() -> int:
    """自检：占位符应放行，真凭证应拦截。"""
    # 注意：这里的取值必须是【人工合成的字符串】，绝不能粘贴任何真实凭证。
    # （曾发生过把真实 key 贴进测试用例、结果被本扫描器拦下的事故 —— 请把这里
    #   当作「不可放真实值」的硬性约束。）
    #
    # 另外这些字符串特意用拼接构造：否则它们本身就是「看起来像凭证」的文本，
    # 会导致本扫描器扫描自身时把自己拦下来。运行时拼接 = 源码里不出现完整模式。
    fake_key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    fake_pw = "Tr0ub4dorStyle" + "Passw0rd"
    cases = [
        ('api_key = "sk-xxx"', True),
        (f'api_key = "{fake_key}"', False),
        ('password = ""', True),
        (f'password = "{fake_pw}"', False),
        ('token = "0000000000000000000000000000dead"', True),
    ]
    ok = True
    for snippet, should_pass in cases:
        hits = scan_text("x.py", snippet)
        passed = not hits
        mark = "OK " if passed == should_pass else "FAIL"
        ok &= passed == should_pass
        print(f"  [{mark}] {snippet}")
    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
