"""
CP-Agent: True Agent architecture with LLM function calling.

The LLM autonomously drives the problem generation workflow by calling tools:
  read_file, write_file, edit_file, list_files,
  compile_cpp, generate_test_data, validate_inputs, run_solution,
  stress_test, search_problem_db, web_search
"""
import json
import logging
from pathlib import Path
from typing import Optional

import config
from config import PROBLEMS_DIR
from dedup import dedup_check as _dedup_check
from dedup import search_problem_db as _search_problem_db  # noqa: F401 — 兼容旧引用
from llm_client import append_assistant_turn, append_tool_results
from llm_client import call_llm_with_tools as _llm_call_with_tools
from pipeline import execute_tool, tool_schemas
from prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: F401 — 对外再导出

_logger = logging.getLogger("cp_agent.agent")


def _validate_problem_md_chinese(problem_dir: Path) -> dict:
    """Check whether problem.md is primarily written in Chinese."""
    if problem_dir is None:
        return {"success": False, "message": "未指定 problem 目录"}

    problem_path = problem_dir / "problem.md"
    if not problem_path.exists():
        return {"success": False, "message": "problem.md 不存在"}

    try:
        content = problem_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"success": False, "message": f"读取 problem.md 失败: {e}"}

    in_code_block = False
    text_parts = []
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            text_parts.append(line)
    text = "\n".join(text_parts)

    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin_count = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    total = cjk_count + latin_count
    cjk_ratio = (cjk_count / total) if total else 0.0

    ok = cjk_count >= 80 and cjk_ratio >= 0.35
    if ok:
        return {
            "success": True,
            "message": f"problem.md 中文校验通过：中文字符 {cjk_count}，中文比例 {cjk_ratio:.2%}",
            "cjk_count": cjk_count,
            "latin_count": latin_count,
            "cjk_ratio": round(cjk_ratio, 4),
        }

    return {
        "success": False,
        "message": (
            "problem.md 中文校验失败：题面必须主要使用中文撰写，"
            f"当前中文字符 {cjk_count}，拉丁字符 {latin_count}，中文比例 {cjk_ratio:.2%}"
        ),
        "cjk_count": cjk_count,
        "latin_count": latin_count,
        "cjk_ratio": round(cjk_ratio, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS — JSON Schema for function calling
# ═══════════════════════════════════════════════════════════════════════════════

# 沙盒工具的 schema 由 pipeline 注册表生成（schema 与实现声明在一起）；
# 这里只额外追加两个非沙盒的搜索类工具。
TOOLS = tool_schemas() + [
    {
        "name": "search_problem_db",
        "description": "原题查重：hybrid 检索本地题库（Codeforces + 洛谷）召回相似题后，系统自动用独立 LLM 裁判比对你的 problem.md 与高分候选，判定是否同一题目模型。必须先写好 problem.md 再调用。裁判判定撞题（must_change）时会拦截后续造数据/对拍等步骤。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查重关键词或题目核心描述，如 '线段树 区间GCD'"},
                "top_k": {"type": "integer", "description": "返回数量，默认 8，建议 5-10"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_search",
        "description": "搜索网页，返回标题、URL 和摘要。可用于查算法资料、参考已有题目、查 testlib 用法等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        }
    },
]





# ═══════════════════════════════════════════════════════════════════════════════
# WEB SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

def _web_search(query: str) -> dict:
    """Search the web using DuckDuckGo HTML (no API key needed)."""
    try:
        import urllib.parse
        import urllib.request

        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Simple extraction of results
        results = []
        import re
        # Extract result blocks
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL):
            href = m.group(1)
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if title and href:
                results.append({"title": title, "url": href})

        # Extract snippets
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', html, re.DOTALL)
        for i, s in enumerate(snippets[:len(results)]):
            results[i]["snippet"] = re.sub(r'<[^>]+>', '', s).strip()[:200]

        if not results:
            return {"success": True, "message": "未找到相关结果", "results": []}

        return {"success": True, "results": results[:5]}
    except Exception as e:
        _logger.exception("web_search failed for query=%r", query)
        return {"success": False, "message": f"搜索失败: {e}"}






def call_llm_with_tools(messages: list[dict], system: str, provider: str,
                        model=None, base_url=None, api_key=None,
                        max_tokens: int = 16000) -> dict:
    """Thin wrapper binding this agent's TOOLS; module-level so tests can patch it."""
    return _llm_call_with_tools(messages, system, provider, TOOLS,
                                model=model, base_url=base_url,
                                api_key=api_key, max_tokens=max_tokens)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT LOOP
# ═══════════════════════════════════════════════════════════════════════════════

# 查重 must_change 时被拒绝执行的产出类工具
_DUP_GATED_TOOLS = {"generate_test_data", "stress_test", "write_metadata",
                    "check_data_strength", "final_check"}

def agent_loop(
    user_prompt: str,
    system_prompt: str = SYSTEM_PROMPT,
    problem_dir: Path = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 16000,
    max_iterations: int = 30,
    tool_defaults: Optional[dict] = None,
    dedup_policy: str = "rewrite",
) -> dict:
    """
    Core agent loop with function calling.

    1. Send user prompt to LLM
    2. If LLM returns tool_use → execute tool → feed result back → repeat
    3. If LLM returns text → done
    4. Cap at max_iterations to prevent infinite loops

    tool_defaults: {tool_name: {arg: value}} — fallback args merged under the
    LLM-provided args, so CLI settings (e.g. --test-count) apply even when the
    LLM omits the argument.

    dedup_policy: 查重判 must_change 时的处置——
      "rewrite"（自由构思模式）：拦截产出类工具并要求换题重写；
      "abort"（完善模式默认）：题意是用户给定的，立即终止并返回撞题详情；
      "warn"（完善模式 --allow-dup）：降级为 manual_review，仅保留裁判理由继续。

    Returns: {"success": bool, "iterations": int, "messages": list, "summary": str}
    """
    from config import get_provider
    protocol, _ = get_provider(provider)

    messages = [{"role": "user", "content": user_prompt}]
    total_input_tokens = 0
    total_output_tokens = 0
    tool_stats: dict[str, int] = {}
    # 查重门禁状态：must_change 时拦截产出类工具，换题并重新查重后解锁
    quality_state = {"search_attempted": False, "dup_verdict": None}

    print(f"\n🤖 Agent loop started (max {max_iterations} iterations)")

    for iteration in range(1, max_iterations + 1):
        print(f"\n  ── Iteration {iteration} ──")

        # Call LLM
        try:
            resp = call_llm_with_tools(
                messages, system_prompt, provider,
                model=model, base_url=base_url, api_key=api_key,
                max_tokens=max_tokens,
            )
        except Exception as e:
            print(f"  ✗ LLM call failed: {e}")
            _logger.exception("LLM call failed at iteration %d", iteration)
            return {
                "success": False,
                "iterations": iteration,
                "messages": messages,
                "summary": f"LLM 调用失败: {e}",
                "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                "tool_stats": tool_stats,
            }

        total_input_tokens += resp.get("usage", {}).get("input", 0)
        total_output_tokens += resp.get("usage", {}).get("output", 0)
        _logger.info("LLM iteration %d: usage=%s", iteration, resp.get("usage"))

        content = resp["content"]

        # Extract text content (for display)
        text_parts = [c["text"] for c in content if c["type"] == "text"]
        tool_parts = [c for c in content if c["type"] == "tool_use"]

        if text_parts:
            for t in text_parts:
                print(f"  💬 {t[:200]}{'...' if len(t) > 200 else ''}")

        if not tool_parts:
            # No tool calls → server-side completion gates before accepting the finish.
            retry_prompt = None

            language_check = _validate_problem_md_chinese(problem_dir)
            if not language_check.get("success"):
                print(f"  ✗ {language_check.get('message')}")
                retry_prompt = (
                    "最终校验失败：problem.md 必须是中文题面。\n"
                    f"{language_check.get('message')}\n\n"
                    "请调用 read_file 读取 problem.md，然后调用 write_file 或 edit_file 将 problem.md "
                    "完整改写为中文题面。要求：标题、题目描述、输入格式、输出格式、样例、样例解释、"
                    "约束和算法说明全部使用中文；保留数学公式、变量名和代码块；不要修改已经通过验证的 "
                    "solution.cpp、generator.cpp、validator.cpp、naive.cpp，除非你发现题面与程序不一致。"
                    "修改后再次给出中文总结。"
                )
            elif not quality_state["search_attempted"]:
                print("  ✗ 完成门禁：从未调用 search_problem_db 查重")
                retry_prompt = (
                    "最终校验失败：你从未调用 search_problem_db 进行原题查重。"
                    "请用题目的算法、核心操作和对象作为 query 调用 search_problem_db，"
                    "确认不与已有题目撞题后再总结。"
                )
            elif quality_state["dup_verdict"] == "must_change":
                print("  ✗ 完成门禁：查重仍为 must_change")
                retry_prompt = (
                    "最终校验失败：最近一次查重存在 final_score ≥ 0.95 的高度相似题。"
                    "必须重写 problem.md 换题、重新生成配套代码和数据，并重新 search_problem_db "
                    "复查通过后才能结束。"
                )
            elif problem_dir is not None:
                print("  🔎 运行服务器端 final_check ...")
                fc = execute_tool(problem_dir, "final_check", {})
                if not fc.get("success"):
                    problems_list = "\n".join(f"- {p}" for p in fc.get("problems", []))
                    print(f"  ✗ final_check 未通过: {fc.get('message')}")
                    retry_prompt = (
                        f"最终校验失败（final_check）：{fc.get('message')}\n"
                        f"{problems_list}\n\n"
                        "请逐项修复上述问题（修复代码/数据后需重新运行受影响的工具，如 compile_cpp、"
                        "generate_test_data、run_solution、write_metadata），"
                        "然后调用 final_check 确认通过，再给出中文总结。"
                    )
                else:
                    print(f"  ✓ {fc.get('message')}")

            if retry_prompt:
                append_assistant_turn(messages, protocol, content, text_parts)
                messages.append({"role": "user", "content": retry_prompt})
                continue

            if language_check.get("success"):
                print(f"  ✓ {language_check.get('message')}")
            # No tool calls and all gates passed → agent is done
            print(f"\n  ✅ Agent finished after {iteration} iterations")
            print(f"  Tokens: {total_input_tokens} in / {total_output_tokens} out")
            return {
                "success": True,
                "iterations": iteration,
                "messages": messages,
                "summary": "".join(text_parts),
                "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                "tool_stats": tool_stats,
            }

        # Process tool calls
        tool_results = []
        for tc in tool_parts:
            tool_name = tc["name"]
            tool_args = {**(tool_defaults or {}).get(tool_name, {}), **tc["input"]}
            tool_id = tc["id"]
            tool_stats[tool_name] = tool_stats.get(tool_name, 0) + 1

            print(f"  🔧 {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")

            # Special handling for non-sandboxed search tools
            if tool_name == "web_search":
                result = _web_search(tool_args.get("query", ""))
            elif tool_name == "search_problem_db":
                result = _dedup_check(
                    problem_dir,
                    tool_args.get("query", ""),
                    tool_args.get("top_k", 8),
                    llm_kwargs={"provider": provider, "model": model,
                                "base_url": base_url, "api_key": api_key},
                )
                quality_state["search_attempted"] = True
                judge_tokens = result.get("judge_tokens") or {}
                total_input_tokens += judge_tokens.get("input", 0)
                total_output_tokens += judge_tokens.get("output", 0)
                if result.get("success"):
                    verdict = result.get("dup_verdict")
                    if verdict == "must_change" and dedup_policy == "abort":
                        dup_desc = "；".join(
                            f"《{d['title']}》({d['source']} {d['source_id']}): {d['reason']}"
                            for d in result.get("duplicates", [])) or result.get("message", "")
                        print(f"  ✗ 题意撞题，按 abort 策略终止：{dup_desc[:200]}")
                        return {
                            "success": False,
                            "iterations": iteration,
                            "messages": messages,
                            "summary": (f"给定题意与已有原题为同一题目模型：{dup_desc}。"
                                        "确认无妨可加 --allow-dup 重跑"),
                            "aborted_dup": True,
                            "duplicates": result.get("duplicates", []),
                            "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                            "tool_stats": tool_stats,
                        }
                    if verdict == "must_change" and dedup_policy == "warn":
                        verdict = "manual_review"
                        result["dup_verdict"] = "manual_review"
                        result["message"] += "（--allow-dup 已生效：撞题降级为警告，继续生成）"
                        print("  ⚠ 题意撞题，--allow-dup 生效，继续")
                    quality_state["dup_verdict"] = verdict
            elif (tool_name in _DUP_GATED_TOOLS
                  and quality_state["dup_verdict"] == "must_change"):
                result = {
                    "success": False,
                    "blocked": True,
                    "message": (
                        "查重未通过（存在 final_score ≥ 0.95 的高度相似题），已拒绝执行此工具。"
                        "必须先重写 problem.md 换一道题，再重新调用 search_problem_db 复查；"
                        "复查通过后才能继续造数据/对拍/写元数据"
                    ),
                }
            else:
                if problem_dir is None:
                    result = {"success": False, "message": "未指定 problem 目录"}
                else:
                    result = execute_tool(problem_dir, tool_name, tool_args)

            # Print result summary (full result goes to the file log)
            status = "✓" if result.get("success") else "✗"
            msg = result.get("message", str(result))[:150]
            print(f"    {status} {msg}")
            _logger.debug("tool %s args=%s result=%s", tool_name,
                          json.dumps(tool_args, ensure_ascii=False),
                          json.dumps(result, ensure_ascii=False))

            tool_results.append({
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "result": result,
            })

        # Add messages in protocol-specific format (adapters in llm_client)
        append_assistant_turn(messages, protocol, content, text_parts, tool_parts)
        append_tool_results(messages, protocol, tool_results)

    # Max iterations reached
    print(f"\n  ⚠ Agent reached max iterations ({max_iterations})")
    return {
        "success": False,
        "iterations": max_iterations,
        "messages": messages,
        "summary": f"达到最大迭代次数 {max_iterations}",
        "tokens": {"input": total_input_tokens, "output": total_output_tokens},
        "tool_stats": tool_stats,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════



def generate_problem(
    topic: str = "",
    difficulty: int = 1500,
    extra: str = "",
    problem_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 16000,
    max_iterations: int = 30,
    test_count: int = 30,
    stress_iterations: Optional[int] = None,
    idea: str = "",
    allow_dup: bool = False,
    cross_check: Optional[bool] = None,
    difficulty_review: Optional[bool] = None,
) -> dict:
    """
    Full agent workflow: LLM drives the entire process via tool calling.

    idea 非空时进入题意完善模式：LLM 在不改变核心题目模型的前提下补全题目；
    撞题时默认中止（allow_dup=True 则降级为警告继续）。
    """
    if not topic and not idea:
        raise ValueError("topic 与 idea 至少提供一个")
    import time
    from datetime import datetime

    from config import get_now_model, get_provider
    protocol, cfg = get_provider(provider)
    resolved_model = model or cfg["default_model"]
    resolved_provider = provider or get_now_model()
    if stress_iterations is None:
        stress_iterations = config.DEFAULT_STRESS_ITERATIONS
    started_at = datetime.now()
    t_start = time.time()

    # Determine problem name: use --name if given, otherwise timestamp
    if not problem_name:
        from datetime import datetime
        problem_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    problem_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in problem_name)[:50]
    problem_dir = PROBLEMS_DIR / problem_name
    problem_dir.mkdir(parents=True, exist_ok=True)

    mode_desc = "completing idea" if idea else f"on '{topic}'"
    print(f"\n🤖 CP-Agent: Generating problem {mode_desc} (difficulty: {difficulty})")
    print(f"   Provider: {resolved_provider} ({protocol}), Model: {resolved_model}")
    print(f"   Output dir: {problem_dir}")

    # Build user prompt with problem dir context
    user_prompt = build_user_prompt(topic, difficulty, extra,
                                    test_count=test_count,
                                    stress_iterations=stress_iterations,
                                    idea=idea)

    # 半成品目录：已有文件视为题意的一部分，要求增量补全而非推倒重写
    existing = sorted(f.name for f in problem_dir.iterdir()
                      if f.is_file() and f.name not in ("result.json",))
    if existing:
        user_prompt += (
            f"\n\n注意：目录中已存在以下文件：{', '.join(existing)}。"
            "请先用 read_file 逐个查看，在其基础上【增量补全】缺失的部分；"
            "已有内容（尤其是 problem.md 里的题意与设定）视为出题人给定，不得推翻重写，"
            "只允许修正明显的格式/一致性问题。"
        )

    user_prompt += f"\n\n所有文件请写入当前目录（相对路径）。文件操作的根目录已设定为：{problem_dir}"

    dedup_policy = ("warn" if allow_dup else "abort") if idea else "rewrite"

    # Run agent loop; tool_defaults guarantees CLI values apply even if the
    # LLM omits count arguments in its tool calls
    result = agent_loop(
        user_prompt=user_prompt,
        problem_dir=problem_dir,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        dedup_policy=dedup_policy,
        tool_defaults={
            "generate_test_data": {"count": test_count},
            "stress_test": {"count": stress_iterations},
            "write_metadata": {"difficulty": difficulty, "provider": resolved_provider},
        },
    )

    # Tighten success: the agent claiming completion is not enough — the
    # directory must actually contain a complete problem package
    from report import check_artifacts, write_result_json
    artifacts = check_artifacts(problem_dir)
    agent_ok = bool(result.get("success"))
    success = agent_ok and artifacts["complete"]

    failure_reason = None
    if not success:
        if not agent_ok:
            failure_reason = result.get("summary", "agent 未完成")
        else:
            failure_reason = f"产物不完整: {artifacts}"

    # ── 可选质量增强（独立验题 / 难度校准）与自建题目入库 ─────────────────
    if cross_check is None:
        cross_check = config.CROSS_CHECK
    if difficulty_review is None:
        difficulty_review = config.DIFFICULTY_REVIEW
    tokens = dict(result.get("tokens", {}))

    def _fold_tokens(extra: dict) -> None:
        for k, v in (extra or {}).items():
            tokens[k] = tokens.get(k, 0) + v

    verify_llm_kwargs = {"provider": provider, "model": model,
                         "base_url": base_url, "api_key": api_key}
    cross_result = None
    review_result = None

    if success and cross_check:
        import verify
        print("\n  🕵️ 独立验题（cross-check）...")
        cross_result = verify.cross_solve(problem_dir, verify_llm_kwargs)
        _fold_tokens(cross_result.get("tokens"))
        icon = {"passed": "✓", "failed": "✗", "inconclusive": "⚠"}[cross_result["status"]]
        print(f"  {icon} {cross_result['message']}")
        if cross_result["status"] == "failed":
            success = False
            failure_reason = cross_result["message"]

    if success and difficulty_review:
        import verify
        print("\n  📏 难度校准评审 ...")
        review_result = verify.review_difficulty(problem_dir, difficulty, verify_llm_kwargs)
        _fold_tokens(review_result.get("tokens"))
        print(f"  {'⚠' if review_result.get('status') == 'warning' else '✓'} {review_result.get('message')}")

    indexed = None
    if success and config.INDEX_GENERATED:
        from dedup import index_generated_problem
        indexed = index_generated_problem(problem_dir).get("success", False)

    payload = {
        "success": success,
        "problem_name": problem_name,
        "mode": "idea" if idea else "topic",
        "topic": topic,
        **({"idea": idea[:500]} if idea else {}),
        "difficulty": difficulty,
        "provider": resolved_provider,
        "model": resolved_model,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - t_start, 1),
        "iterations": result.get("iterations", 0),
        "tokens": tokens,
        "tool_calls": result.get("tool_stats", {}),
        "artifacts": artifacts,
        **({"cross_check": {k: v for k, v in cross_result.items() if k != "tokens"}}
           if cross_result else {}),
        **({"difficulty_review": {k: v for k, v in review_result.items() if k != "tokens"}}
           if review_result else {}),
        **({"indexed": indexed} if indexed is not None else {}),
        "failure_reason": failure_reason,
    }

    # Failed runs must not linger next to good problems: delete empty dirs,
    # archive non-empty ones under problems/failed/.
    # 例外：目录里有用户预先放置的文件（半成品完善模式）时，失败也原地保留，
    # 不能把用户的草稿挪走。
    if success:
        write_result_json(problem_dir, payload)
    else:
        has_content = any(problem_dir.iterdir())
        if not has_content:
            problem_dir.rmdir()
            problem_dir = None
        elif existing:
            write_result_json(problem_dir, payload)
            print(f"  📄 失败详情已写入 {problem_dir / 'result.json'}（目录含用户草稿，原地保留）")
        else:
            import shutil
            write_result_json(problem_dir, payload)
            failed_root = PROBLEMS_DIR / "failed"
            failed_root.mkdir(parents=True, exist_ok=True)
            dest = failed_root / f"{problem_name}_{started_at.strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(problem_dir), str(dest))
            problem_dir = dest
            print(f"  📦 失败产物已归档到 {dest}")

    result["success"] = success
    result["problem_dir"] = str(problem_dir) if problem_dir else None
    result["problem_name"] = problem_name
    if failure_reason:
        result["summary"] = failure_reason

    if success:
        print(f"\n🎉 Problem package ready: {problem_dir}")
    else:
        print(f"\n⚠️  Agent 未完成: {failure_reason}")

    return result


# Legacy entry point (for --pipeline mode, no LLM)
def run_pipeline_only(problem_dir: Path, test_count: int = 30,
                      stress_iterations: Optional[int] = None,
                      skip_stress: bool = False) -> dict:
    """Run only the pipeline on an existing problem directory (no LLM)."""
    from pipeline import Pipeline
    pipe = Pipeline(problem_dir)
    return pipe.run_full(test_count=test_count, stress_iterations=stress_iterations,
                         skip_stress=skip_stress)
