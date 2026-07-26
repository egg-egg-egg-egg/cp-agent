"""
可选质量增强：独立验题（cross-solve）与难度校准（difficulty review）。

两者都用独立的 LLM 调用扮演"验题人/评审员"角色，与出题的主会话隔离，
用于捕获同源错误（std 和 naive 错得一致时对拍测不出来）和离谱的难度标称。
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

import config
from llm_client import call_llm_text
from pipeline import (
    _child_mem_mb,
    _load_problem_yaml,
    _run_checker,
    _run_cmd,
    _token_compare,
    execute_tool,
)

_logger = logging.getLogger("cp_agent.verify")

CROSS_SOLVE_SYSTEM = """\
你是一名顶尖算法竞赛选手。阅读题面后独立解题，用 C++17 写出完整、高效、正确的程序：
- 从标准输入读取，向标准输出打印，不要有任何多余输出
- 不要使用 bits/stdc++.h（用标准头文件）
- 只输出一个 ```cpp 代码块，不要输出任何解释文字"""

DIFFICULTY_REVIEW_SYSTEM = """\
你是资深算法竞赛出题人，请评估这道题的 Codeforces rating（800-3500，步长 100）。
评估依据：需要的算法/数据结构、思维跳跃步数、实现难度、数据范围带来的复杂度要求。
只输出一个 JSON 对象：{"estimated": 1700, "reason": "一句话理由"}"""


def strip_editorial(statement: str) -> str:
    """剥离题解章节，让验题人只看选手视角的题面。"""
    m = re.search(r"^#{1,4}\s*.*(题解|解法|做法|editorial)", statement,
                  re.MULTILINE | re.IGNORECASE)
    return statement[:m.start()].rstrip() if m else statement


def extract_cpp(text: str) -> str:
    """从 LLM 输出中提取 C++ 代码块。"""
    m = re.search(r"```(?:cpp|c\+\+)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    if "#include" in text:  # 没包代码围栏但看起来是代码
        return text
    raise ValueError("验题人输出中未找到 C++ 代码块")


def _resolve_kwargs(model_key: str, llm_kwargs: Optional[dict]) -> dict:
    """model_key 配置了独立 provider 时优先，否则沿用主调用参数。"""
    override = getattr(config, model_key)
    if override:
        return {"provider": override}
    return dict(llm_kwargs or {})


def cross_solve(problem_dir: Path, llm_kwargs: Optional[dict] = None,
                max_attempts: int = 2) -> dict:
    """
    独立验题：另一次隔离的 LLM 调用只看题面（剥离题解）盲解，
    编译后在全部现有测试点上与标程输出比对（SPJ 走 checker）。
    返回 {"status": "passed"|"failed"|"inconclusive", ...}
    仅 WA 视为 failed（验题人程序 TLE/RE 只能说明该测试点无法验证）。
    """
    base = Path(problem_dir)
    statement = strip_editorial(
        (base / "problem.md").read_text(encoding="utf-8", errors="replace"))
    kwargs = _resolve_kwargs("CROSS_CHECK_MODEL", llm_kwargs)
    usage: dict = {}

    # 1. 独立解题 + 编译（编译错误反馈重试一次）
    compile_error = ""
    compiled = False
    attempts = 0
    for attempts in range(1, max_attempts + 1):
        user = f"【题面】\n{statement}"
        if compile_error:
            user += f"\n\n你上一份代码编译失败，请修复后重新给出完整代码：\n{compile_error[:800]}"
        try:
            raw = call_llm_text(CROSS_SOLVE_SYSTEM, user, max_tokens=4000,
                                usage_sink=usage, **kwargs)
            code = extract_cpp(raw)
        except Exception as e:
            _logger.exception("cross_solve LLM call failed")
            return {"status": "inconclusive", "message": f"验题人调用失败: {e}",
                    "attempts": attempts, "tokens": usage}
        (base / "cross_solution.cpp").write_text(code, encoding="utf-8")
        r = execute_tool(base, "compile_cpp",
                         {"source": "cross_solution.cpp", "output": "bin/cross_solution"})
        if r.get("success"):
            compiled = True
            break
        compile_error = r.get("message", "")

    if not compiled:
        return {"status": "inconclusive",
                "message": f"验题人程序 {max_attempts} 次尝试均编译失败: {compile_error[:200]}",
                "attempts": attempts, "tokens": usage}

    # 2. 全测试点比对
    meta = _load_problem_yaml(base) or {}
    tl_sec = (meta.get("time_limit_ms") or config.DEFAULT_TIME_LIMIT_MS) / 1000.0
    run_timeout = max(int(tl_sec * 3) + 1, 10)   # 验题人程序允许比标程慢
    mem_mb = _child_mem_mb(base)
    use_checker = (base / "bin" / "checker").exists()
    cross_bin = base / "bin" / "cross_solution"

    verified, skipped, mismatches = 0, [], []
    for f in sorted((base / "inputs").glob("*.in")):
        expected = base / "outputs" / (f.stem + ".out")
        if not expected.exists():
            continue
        inp = f.read_text()
        code_rc, out, _err = _run_cmd([str(cross_bin)], cwd=str(base),
                                      stdin_data=inp, timeout=run_timeout, mem_mb=mem_mb)
        if code_rc != 0:
            skipped.append({"file": f.name, "why": "TLE" if code_rc == -1 else f"exit={code_rc}"})
            continue
        exp_text = expected.read_text()
        if use_checker:
            verdict, cmsg = _run_checker(base, inp, out, exp_text)
            ok = verdict == "AC"
            detail = f"checker={verdict}: {cmsg[:100]}"
        else:
            ok = _token_compare(out, exp_text)
            detail = f"cross: {out.strip()[:80]!r} vs std: {exp_text.strip()[:80]!r}"
        if ok:
            verified += 1
        else:
            mismatches.append({"file": f.name, "detail": detail})
            if len(mismatches) >= 3:
                break

    if mismatches:
        status, message = "failed", (
            f"独立验题不一致：{len(mismatches)} 个测试点上验题人解与标程输出不同"
            "（题面有歧义或标程有错，需人工排查）"
        )
    elif verified == 0:
        status, message = "inconclusive", "验题人程序在所有测试点上超时/出错，无法验证"
    else:
        status, message = "passed", f"独立验题通过：{verified} 个测试点一致（跳过 {len(skipped)} 个）"

    return {"status": status, "message": message, "verified": verified,
            "skipped": skipped, "mismatches": mismatches,
            "attempts": attempts, "tokens": usage}


def review_difficulty(problem_dir: Path, declared: int,
                      llm_kwargs: Optional[dict] = None) -> dict:
    """独立评审员估计 CF rating，与标称难度偏差 > 300 时给出警告（不判失败）。"""
    base = Path(problem_dir)
    statement = (base / "problem.md").read_text(encoding="utf-8", errors="replace")[:6000]
    kwargs = _resolve_kwargs("DIFFICULTY_REVIEW_MODEL", llm_kwargs)
    if not getattr(config, "DIFFICULTY_REVIEW_MODEL") and config.DEDUP_JUDGE_MODEL \
            and "provider" not in (llm_kwargs or {}):
        kwargs = {"provider": config.DEDUP_JUDGE_MODEL}
    usage: dict = {}

    try:
        raw = call_llm_text(DIFFICULTY_REVIEW_SYSTEM,
                            f"【题面（含题解）】\n{statement}\n\n【出题人标称难度】CF {declared}",
                            max_tokens=500, usage_sink=usage, **kwargs)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        estimated = int(data["estimated"])
    except Exception as e:
        _logger.exception("difficulty review failed")
        return {"status": "error", "message": f"难度评审调用失败: {e}", "tokens": usage}

    delta = estimated - int(declared)
    warning = abs(delta) > 300
    return {
        "status": "warning" if warning else "ok",
        "declared": int(declared),
        "estimated": estimated,
        "delta": delta,
        "reason": str(data.get("reason", ""))[:300],
        "message": (f"难度校准：标称 {declared}，评审估计 {estimated}（偏差 {delta:+d}）"
                    + ("，偏差过大，建议人工复核" if warning else "")),
        "tokens": usage,
    }
