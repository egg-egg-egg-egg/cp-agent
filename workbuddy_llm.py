"""
WorkBuddy LLM driver —— 用 WorkBuddy 会话内的智能体替换 API-key 型 LLM。

设计要点
--------
cp-agent 原本把每一轮 LLM 调用发给 OpenAI/Anthropic 兼容接口（需要 api_key）。
本模块提供第三种协议 `workbuddy`：

    不发网络请求，而是把完整调用上下文写成一个 JSON 请求文件丢进队列目录，
    然后阻塞等待队列里出现同名 `.resp` 响应文件。
    响应文件由 WorkBuddy（会话里的智能体，或人）写入，内容可以是纯文本、
    单个工具调用 JSON、或完整的 content 数组。

这样"出题"这件事的模型能力由 WorkBuddy 提供，项目不再依赖任何 api_key。

队列目录结构（默认 `<项目根>/.llm_queue/`）：

    .llm_queue/
    ├── pending/          待处理请求   <id>.json
    │                     响应写这里   <id>.resp
    └── done/             已完成的请求与响应

环境变量（优先级高于 config.yaml）：
    CP_AGENT_WB_QUEUE_DIR    队列目录（默认 <项目根>/.llm_queue）
    CP_AGENT_WB_TIMEOUT      单次等待秒数（默认 3600）
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

DRIVER_NAME = "workbuddy"
DEFAULT_QUEUE_DIRNAME = ".llm_queue"
DEFAULT_TIMEOUT_SEC = 3600

# 项目根：本文件所在目录即项目根
PROJECT_ROOT = Path(__file__).resolve().parent


# ────────────────────────────── 队列目录 ──────────────────────────────

def resolve_queue_dir(cfg: Optional[dict] = None) -> Path:
    """确定队列目录：环境变量 > provider 配置 queue_dir > 项目根/.llm_queue。"""
    env = os.environ.get("CP_AGENT_WB_QUEUE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    q = (cfg or {}).get("queue_dir") or DEFAULT_QUEUE_DIRNAME
    p = Path(q)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def resolve_timeout(cfg: Optional[dict] = None) -> int:
    env = os.environ.get("CP_AGENT_WB_TIMEOUT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        return int((cfg or {}).get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC


def _ensure_dirs(queue_dir: Path) -> tuple[Path, Path]:
    pending = queue_dir / "pending"
    done = queue_dir / "done"
    pending.mkdir(parents=True, exist_ok=True)
    done.mkdir(parents=True, exist_ok=True)
    return pending, done


def new_request_id() -> str:
    """形如 013516-a1b2c3：时间前缀便于排序，随机后缀避免并发撞号。"""
    return f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"


# ────────────────────────────── 请求 / 响应 ──────────────────────────────

INSTRUCTIONS = """你是 cp-agent 的 LLM 后端（workbuddy driver，无需 api_key）。

请把本轮回复写入 response_path 指向的文件（UTF-8 纯文本），三选一：

1) 纯文本 —— 直接写回复内容，表示本轮结束、不再调用工具。

2) 单个工具调用 ——
   {"tool": "write_file", "input": {"path": "problem.md", "content": "..."}}

3) 完整 content 数组（可含多段文本 + 多个工具调用）——
   {"content": [
     {"type": "text", "text": "我先写题面"},
     {"type": "tool_use", "name": "write_file", "input": {"path": "problem.md", "content": "..."}}
   ]}

可调用的工具名与参数 schema 见 tools 字段；上下文见 system 与 messages。
写完响应文件后 cp-agent 会自动继续（不需要额外通知）。
若判定本轮应中止，改为创建 cancel_path 文件即可。
"""


def submit_request(*, queue_dir: Path, req_id: str, system: str,
                   messages: list[dict], tools: list[dict],
                   model: str, max_tokens: int, stage: str = "") -> dict:
    """把一次 LLM 调用落成 pending/<id>.json，返回请求 dict（含响应路径）。"""
    pending, _ = _ensure_dirs(queue_dir)
    req_path = pending / f"{req_id}.json"
    resp_path = pending / f"{req_id}.resp"
    cancel_path = pending / f"{req_id}.cancel"

    req = {
        "id": req_id,
        "driver": DRIVER_NAME,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage or "llm_call",
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "tools": [
            {"name": t.get("name"), "description": t.get("description"),
             "input_schema": t.get("input_schema")}
            for t in (tools or [])
        ],
        "instructions": INSTRUCTIONS,
        "response_path": str(resp_path),
        "cancel_path": str(cancel_path),
    }
    req_path.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    return req


def wait_for_response(req: dict, timeout_sec: int, poll: float = 0.5,
                      quiet: bool = False) -> str:
    """阻塞等待响应文件出现；返回响应文本。超时或取消时抛异常。"""
    resp_path = Path(req["response_path"])
    cancel_path = Path(req["cancel_path"])
    deadline = time.time() + timeout_sec
    last_beat = 0.0

    if not quiet:
        print(f"  ⏳ 等待 WorkBuddy 应答 → 写入响应文件：{resp_path}", flush=True)

    while True:
        if cancel_path.exists():
            try:
                cancel_path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"WorkBuddy 取消了本次调用（{cancel_path.name}）")
        if resp_path.exists():
            text = resp_path.read_text(encoding="utf-8")
            if text.strip():
                return text
            # 空文件：可能正在写入，稍等
        now = time.time()
        if now - last_beat >= 30:
            left = int(deadline - now)
            print(f"  ⏳ 仍在等待 WorkBuddy 应答（剩余 {max(left, 0)}s）…", flush=True)
            last_beat = now
        if now >= deadline:
            raise TimeoutError(
                f"等待 WorkBuddy 应答超时（{timeout_sec}s）：{resp_path} 未产生响应")
        time.sleep(poll)


def archive_request(queue_dir: Path, req_id: str) -> None:
    """把已完成的请求与响应移入 done/。"""
    pending, done = _ensure_dirs(queue_dir)
    for suffix in (".json", ".resp"):
        src = pending / f"{req_id}{suffix}"
        if src.exists():
            try:
                shutil.move(str(src), str(done / src.name))
            except OSError:
                pass


# ────────────────────────────── 响应解析 ──────────────────────────────

def _strip_fence(text: str) -> str:
    """去掉 ```json ... ``` 代码围栏。"""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_response(raw: str) -> list[dict]:
    """
    把响应文本解析成 cp-agent 统一的 content blocks：
      [{"type":"text","text":...}, {"type":"tool_use","id":...,"name":...,"input":{...}}]
    容错：非 JSON 一律按纯文本处理。
    """
    text = _strip_fence(raw or "")
    if not text:
        return []

    obj = None
    if text[:1] in "{[":
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            obj = None

    blocks: list[dict] = []

    def _one(item: dict, idx: int) -> None:
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": item.get("text", "")})
            return
        name = item.get("name") or item.get("tool")
        if kind in ("tool_use", "tool") or name:
            blocks.append({
                "type": "tool_use",
                "id": item.get("id") or f"wb_{idx}",
                "name": name,
                "input": item.get("input") or {},
            })
            return
        if item.get("text") is not None:
            blocks.append({"type": "text", "text": item["text"]})

    if isinstance(obj, dict):
        if isinstance(obj.get("content"), list):
            for i, b in enumerate(obj["content"]):
                _one(b, i)
            return blocks
        _one(obj, 0)
        if blocks:
            return blocks
    elif isinstance(obj, list):
        for i, b in enumerate(obj):
            _one(b, i)
        if blocks:
            return blocks

    return [{"type": "text", "text": text}]


def _estimate_tokens(*chunks: str) -> int:
    return max(1, sum(len(c or "") for c in chunks) // 4)


# ────────────────────────────── 对外主入口 ──────────────────────────────

def call_workbuddy_with_tools(messages: list[dict], system: str, tools: list[dict],
                              cfg: Optional[dict] = None, model: str = "",
                              max_tokens: int = 16000, stage: str = "",
                              timeout_sec: Optional[int] = None,
                              quiet: bool = False) -> dict:
    """
    走 WorkBuddy 队列完成一次（可带工具的）LLM 调用。
    返回与 OpenAI/Anthropic 分支一致的统一格式：{stop_reason, content, usage}
    """
    queue_dir = resolve_queue_dir(cfg)
    timeout = timeout_sec if timeout_sec is not None else resolve_timeout(cfg)
    req_id = new_request_id()

    req = submit_request(queue_dir=queue_dir, req_id=req_id, system=system,
                         messages=messages, tools=tools, model=model or DRIVER_NAME,
                         max_tokens=max_tokens, stage=stage)
    try:
        raw = wait_for_response(req, timeout, quiet=quiet)
    except BaseException:
        archive_request(queue_dir, req_id)
        raise

    blocks = parse_response(raw)
    archive_request(queue_dir, req_id)

    usage = {
        "input": _estimate_tokens(system, json.dumps(messages, ensure_ascii=False)),
        "output": _estimate_tokens(raw),
    }
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"stop_reason": stop, "content": blocks, "usage": usage}


# ────────────────────────────── CLI ──────────────────────────────

def _cli_list(queue_dir: Path) -> int:
    pending, done = _ensure_dirs(queue_dir)
    reqs = sorted(pending.glob("*.json"))
    if not reqs:
        print(f"（无待处理请求）队列：{pending}")
        return 0
    print(f"待处理 {len(reqs)} 条，队列：{pending}")
    for p in reqs:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            print(f"  {p.name}  <无法解析>")
            continue
        n_tools = len(d.get("tools") or [])
        n_msg = len(d.get("messages") or [])
        print(f"  {d.get('id')}  stage={d.get('stage')}  消息={n_msg}  工具={n_tools}  "
              f"created={d.get('created_at')}")
        print(f"      响应请写入: {d.get('response_path')}")
    return 0


def _cli_show(queue_dir: Path, req_id: str) -> int:
    pending, done = _ensure_dirs(queue_dir)
    for base in (pending, done):
        p = base / f"{req_id}.json"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
            return 0
    print(f"找不到请求 {req_id}（已查 {pending} 与 {done}）")
    return 1


def _cli_answer(queue_dir: Path, req_id: str, file: Optional[str],
                text: Optional[str]) -> int:
    pending, _ = _ensure_dirs(queue_dir)
    resp = pending / f"{req_id}.resp"
    if file:
        content = Path(file).read_text(encoding="utf-8")
    elif text is not None:
        content = text
    else:
        content = __import__("sys").stdin.read()
    resp.write_text(content, encoding="utf-8")
    print(f"已写入响应：{resp}（{len(content)} 字符）")
    return 0


def _cli_next(queue_dir: Path, timeout_sec: int) -> int:
    """阻塞等待下一条请求，打印它的 id（便于脚本接力）。"""
    pending, _ = _ensure_dirs(queue_dir)
    deadline = time.time() + timeout_sec
    seen = set(p.name for p in pending.glob("*.json"))
    while time.time() < deadline:
        for p in sorted(pending.glob("*.json")):
            if p.name not in seen and not p.with_suffix(".resp").exists():
                print(p.stem)
                return 0
        time.sleep(0.5)
    print("（超时，无新请求）")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="workbuddy_llm",
        description="WorkBuddy LLM driver 队列工具（list/show/answer/next）")
    ap.add_argument("--queue-dir", default=None, help="队列目录（默认 <项目根>/.llm_queue）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出待处理请求")
    p_show = sub.add_parser("show", help="打印请求全文")
    p_show.add_argument("req_id")
    p_ans = sub.add_parser("answer", help="写入响应")
    p_ans.add_argument("req_id")
    p_ans.add_argument("--file", help="从文件读取响应内容")
    p_ans.add_argument("--text", help="直接给出响应内容")
    p_next = sub.add_parser("next", help="阻塞等待下一条请求并打印 id")
    p_next.add_argument("--timeout", type=int, default=600)

    args = ap.parse_args(argv)
    qdir = Path(args.queue_dir).resolve() if args.queue_dir else resolve_queue_dir()

    if args.cmd == "list":
        return _cli_list(qdir)
    if args.cmd == "show":
        return _cli_show(qdir, args.req_id)
    if args.cmd == "answer":
        return _cli_answer(qdir, args.req_id, args.file, args.text)
    if args.cmd == "next":
        return _cli_next(qdir, args.timeout)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
