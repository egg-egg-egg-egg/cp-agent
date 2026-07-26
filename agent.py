"""
CP-Agent: True Agent architecture with LLM function calling.

The LLM autonomously drives the problem generation workflow by calling tools:
  read_file, write_file, edit_file, list_files,
  compile_cpp, generate_test_data, validate_inputs, run_solution,
  stress_test, search_problem_db, web_search
"""
import json
from pathlib import Path
from typing import Optional

import config
from config import PROBLEMS_DIR
from pipeline import execute_tool


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

TOOLS = [
    {
        "name": "read_file",
        "description": "读取文件内容。路径必须是相对路径（沙盒限制在 problem 目录内）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径，如 solution.cpp 或 inputs/01.in"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "创建或覆写文件。路径必须是相对路径。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径，如 solution.cpp"},
                "content": {"type": "string", "description": "文件完整内容"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "搜索替换修改文件。old_text 必须与文件内容完全匹配（包括缩进和换行）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径"},
                "old_text": {"type": "string", "description": "要替换的原始文本（必须完全匹配）"},
                "new_text": {"type": "string", "description": "替换后的新文本"}
            },
            "required": ["path", "old_text", "new_text"]
        }
    },
    {
        "name": "list_files",
        "description": "列出目录内容（文件和子目录）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "dir": {"type": "string", "description": "相对路径，默认 '.'"}
            },
        }
    },
    {
        "name": "compile_cpp",
        "description": "编译 C++ 文件。需要指定源文件和输出路径。输出路径通常为 bin/xxx。",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源文件相对路径，如 solution.cpp"},
                "output": {"type": "string", "description": "输出二进制相对路径，如 bin/solution"}
            },
            "required": ["source", "output"]
        }
    },
    {
        "name": "generate_test_data",
        "description": "运行已编译的 generator 生成测试数据。generator 必须先编译为 bin/generator。",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "生成的测试数据数量，默认 30"}
            },
        }
    },
    {
        "name": "validate_inputs",
        "description": "运行已编译的 validator 校验所有 inputs/*.in 文件。validator 必须先编译为 bin/validator。",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "run_solution",
        "description": "运行已编译的 solution，为每个 inputs/*.in 生成对应的 outputs/*.out。solution 必须先编译为 bin/solution。",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "stress_test",
        "description": "对拍验证：运行 generator 生成随机输入（自动附加 argv[3]='stress' 提示生成小数据），分别运行 solution 和 naive，比较输出。若已编译 bin/checker 则用 checker 判定（支持多解 SPJ 题），否则按 token 精确比对。solution 和 naive 必须先编译。",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "对拍轮数，默认 1000"}
            },
        }
    },
    {
        "name": "write_metadata",
        "description": "生成 problem.yaml 元数据文件。你提供标题、算法标签、难度和时限/内存限制；测试点列表和 checker 类型由系统扫描目录自动生成。必须在 stress_test 通过之后调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "题目标题（中文）"},
                "algorithm_tags": {"type": "array", "items": {"type": "string"},
                                   "description": "算法标签，如 ['动态规划', '前缀和']"},
                "difficulty": {"type": "integer", "description": "Codeforces rating，如 1500"},
                "time_limit_ms": {"type": "integer", "description": "时间限制（毫秒），默认 1000"},
                "memory_limit_mb": {"type": "integer", "description": "内存限制（MB），默认 256"},
                "subtasks": {"type": "array", "description": "可选：子任务列表 [{id, score, cases, constraints}]"}
            },
            "required": ["title", "algorithm_tags"]
        }
    },
    {
        "name": "check_data_strength",
        "description": "数据强度检查：在体积最大的几个测试点上运行 solution 和 naive。要求 solution 在时限内通过，且 naive 至少在一个大测试点上超时（或 ≥5× solution 耗时）。注意：这里 naive 超时是好事（说明数据能卡掉暴力）；全部轻松通过说明数据太弱，需要增大 generator 的最大规模。",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "final_check",
        "description": "出题完成前的最终检查：必需文件齐全、inputs/outputs 与 problem.yaml 一致、题面样例与 solution 输出一致（SPJ 用 checker）、约束边界被测试点触达、数据强度通过。全部通过后自动回填 problem.yaml 的 validation 块。总结前必须通过此检查。",
        "input_schema": {
            "type": "object",
            "properties": {
                "waive_bounds": {"type": "array", "items": {"type": "string"},
                                 "description": "显式豁免的未触达边界，如 ['a[i].max']，需有正当理由"}
            },
        }
    },
    {
        "name": "search_problem_db",
        "description": "搜索本地竞赛题库（Codeforces + 洛谷）的相似题，用于原题查重。基于 hybrid 检索：FAISS 向量 + 关键词 + 结构化术语 rerank。构思题目后必须优先调用此工具。",
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


# Convert to OpenAI tools format (used by OpenAI-compatible providers)
def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-style tool defs to OpenAI tools format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }
        })
    return result


OPENAI_TOOLS = _to_openai_tools(TOOLS)


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
你是 CP-Agent，一个算法竞赛出题专家。你可以通过调用工具来完成整个出题流程。

## 可用工具
- read_file(path) — 读取文件
- write_file(path, content) — 创建/覆写文件
- edit_file(path, old_text, new_text) — 搜索替换修改文件
- list_files(dir) — 列出目录
- compile_cpp(source, output) — 编译 C++
- generate_test_data(count) — 生成测试数据
- validate_inputs() — 校验输入
- run_solution() — 运行标程生成输出
- stress_test(count) — 对拍验证
- write_metadata(title, algorithm_tags, ...) — 生成 problem.yaml 元数据
- check_data_strength() — 数据强度检查（naive 必须被大数据卡掉）
- final_check(waive_bounds?) — 最终检查（文件/样例/边界覆盖/数据强度）
- search_problem_db(query, top_k) — 搜索本地题库相似题，用于原题查重
- web_search(query) — 搜索网页

## 工作流程
1. 构思题目，生成 problem.md（题面）
2. 必须用 search_problem_db 搜索题目关键词和核心模型，检查是否与已有题目重复。至少搜索 1 次，建议 query 包含算法、数据结构、核心操作和题目对象
3. 如果本地题库搜索结果中出现高度相似的题（题目模型、输入输出、目标函数或核心操作几乎一样），必须换一个题目重新构思。web_search 只作为补充资料搜索，不作为主查重工具
4. 生成 solution.cpp, generator.cpp, validator.cpp, naive.cpp；若题目是多解题（见下方"何时需要 checker"），还必须生成 checker.cpp
5. 编译所有 C++ 文件（输出到 bin/ 目录；checker.cpp 编译为 bin/checker）
6. 运行 generator 生成测试数据（数量以用户要求为准，含边界数据）
7. 运行 validator 校验输入数据
8. 运行 solution 生成输出
9. 运行 stress_test 对拍验证（轮数以用户要求为准）
10. 调用 write_metadata 写入 problem.yaml（标题、算法标签、难度、时限/内存限制）
11. 调用 check_data_strength 确认数据能卡掉暴力（不通过则增大 generator 最大规模并重新造数据）
12. 调用 final_check 做最终检查，全部通过后才能总结
13. 如果任何步骤出错，检查错误、修复代码、重试

## 何时需要 checker（special judge）
以下情况必须写 checker.cpp 并编译为 bin/checker：
- 答案不唯一（构造题、"输出任意一组合法方案"、多个最优解）
- 浮点输出（需要相对/绝对误差比较）
- 输出顺序不定（如任意顺序输出集合元素）
答案唯一的题目禁止写 checker（保持默认 token 比对即可）。

checker.cpp 模板（testlib，调用约定 checker <input> <output> <answer>）：
```cpp
#include "testlib.h"
using namespace std;
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    // inf=测试输入 ouf=被检查的输出 ans=标程答案（仅作参照）
    // 关键：必须仅凭 inf（+可选 ans 中的目标值）验证 ouf 的合法性/最优性，
    // 不能与 ans 逐字比较——对拍时 naive 输出只是"某一个"合法解
    // 不合法时 quitf(_wa, "原因")；合法时：
    quitf(_ok, "correct");
}
```
注意：写了 checker 的题，naive.cpp 也必须输出合法解（对拍时 naive 输出作为参照答案传给 checker）。

## 查重判定
- search_problem_db 返回的 final_score 越高越相似；重点看 title、source、source_id、matched_terms 和 snippet
- final_score >= 0.95 且题目模型/操作相近：视为高风险撞题，必须换题
- final_score 0.75-0.95：人工判断相似点，若只是同算法模板但叙事/目标/约束不同可以继续，否则换题
- final_score < 0.75：通常可继续，但仍需避开明显同题

## 重要规则
- problem.md 必须使用中文撰写。标题、题目描述、输入格式、输出格式、样例、样例解释、约束和题解说明都必须是中文；可以保留必要的英文变量名、数学符号和代码块
- 如果你发现 problem.md 是英文或主要不是中文，必须在继续编译/造数据前调用 write_file 或 edit_file 将其完整翻译/改写为中文
- 最终总结前必须确保 problem.md 是中文题面；否则不要结束任务
- generator.cpp 必须基于 testlib.h，使用 #include "testlib.h"
- validator.cpp 必须基于 testlib.h
- testlib.h 位于项目根目录，编译时 -I 会自动包含
- generator 必须接收 argv[1]（测试编号）和 argv[2]（总数）作为参数；argv[3] 可能为 "stress"（对拍模式），此时必须生成小规模数据（如 n ≤ 500），保证 naive 能在几秒内跑完
- solution.cpp 必须是高效正确的解法，复杂度必须匹配数据规模
- naive.cpp 必须是暴力/朴素解法（用于对拍）
- 所有文件操作必须使用相对路径
- C++ 编译使用 g++ -std=c++17 -O2 -Wall -Wextra
- macOS 没有 bits/stdc++.h，请使用标准头文件（iostream, vector, algorithm 等）

## generator.cpp 模板
```cpp
#include "testlib.h"
#include <iostream>
using namespace std;
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int idx = atoi(argv[1]);
    int total = atoi(argv[2]);
    // 对拍模式：stress_test 会传 argv[3]="stress"，此时必须用小规模数据
    bool stress = (argc > 3 && string(argv[3]) == "stress");
    int maxN = stress ? 500 : 100000;  // 根据难度调整正式上限
    int n = rnd.next(1, maxN);
    cout << n << endl;
    for (int i = 0; i < n; i++) {
        cout << rnd.next(1, 1000);
        if (i + 1 < n) cout << " ";
    }
    cout << endl;
    return 0;
}
```

## validator.cpp 模板（重要：必须这样写）
validator 使用 registerValidation，从 stdin 读取输入；给每个变量命名（readInt 的第三个参数），
系统会据此统计"每个约束的 min/max 边界是否被测试数据触达"：
```cpp
#include "testlib.h"
#include <iostream>
using namespace std;
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    int n = inf.readInt(1, 100000, "n");
    inf.readEoln();
    for (int i = 0; i < n; i++) {
        inf.readInt(1, 1000000, "a[i]");
        if (i + 1 < n) inf.readSpace();
    }
    inf.readEoln();
    inf.readEof();
    return 0;
}
```
注意：必须用 registerValidation(argc, argv)（不要用 registerGen/inf.init/registerTestlibCmd）；
readInt/readLong 必须带变量名参数，否则边界覆盖检查无法工作。

## 超时处理
如果 run_solution 返回 timeout: true，说明标程复杂度过高，必须：
1. 检查 solution.cpp 的算法复杂度
2. 优化算法（如 O(n²) → O(n log n)）
3. 重新编译并运行
超时意味着标程是错误的，不能忽略

## 最终输出
当所有步骤完成后，用中文总结：
- 题目名称和算法考点
- 数据规模和限制
- 生成了多少组测试数据
- 对拍结果
"""


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
        return {"success": False, "message": f"搜索失败: {e}"}


def _search_problem_db(query: str, top_k: int = 8) -> dict:
    """Search local problem DB with hybrid retrieval for duplicate checking."""
    try:
        from problem_db import DB_PATH, INDEX_PATH
        from problem_db.hybrid_search import hybrid_search

        top_k = max(1, min(int(top_k or 8), 20))
        raw_results = hybrid_search(DB_PATH, INDEX_PATH, query, top_k=top_k)

        results = []
        for r in raw_results:
            content = (r.get("content") or r.get("llm_standardized") or "")
            content = " ".join(str(content).split())
            results.append({
                "rank": r.get("rank"),
                "final_score": round(float(r.get("final_score", 0.0)), 4),
                "vector_score": round(float(r.get("vector_score", 0.0)), 4),
                "source": r.get("source", ""),
                "source_id": r.get("source_id", ""),
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "tags": r.get("tags", ""),
                "difficulty": r.get("difficulty", ""),
                "matched_terms": r.get("matched_terms", []),
                "term_coverage": r.get("term_coverage", 0),
                "term_strength": r.get("term_strength", 0),
                "snippet": content[:350],
            })

        top_score = max((r["final_score"] for r in results), default=0.0)
        if top_score >= 0.95:
            dup_verdict = "must_change"
            verdict_note = (
                "查重结论【强制】：存在 final_score ≥ 0.95 的高度相似题，必须重写 problem.md 换题，"
                "并重新调用 search_problem_db 复查。复查通过前，"
                "generate_test_data/stress_test/write_metadata/final_check 会被拒绝执行。"
            )
        elif top_score >= 0.75:
            dup_verdict = "manual_review"
            verdict_note = (
                "查重结论：final_score 在 0.75-0.95 之间，请对比 title/snippet/matched_terms 判断："
                "若只是同算法模板但叙事/目标/约束不同可继续；若题目模型几乎一样必须换题并重新查重。"
            )
        else:
            dup_verdict = "ok"
            verdict_note = "查重结论：未发现高度相似题，可以继续。"

        return {
            "success": True,
            "message": f"本地题库查重完成：返回 {len(results)} 条，最高相似度 {top_score:.2f}。{verdict_note}",
            "query": query,
            "top_score": top_score,
            "dup_verdict": dup_verdict,
            "results": results,
        }
    except Exception as e:
        return {"success": False, "message": f"本地题库搜索失败: {e}", "query": query}


# ═══════════════════════════════════════════════════════════════════════════════
# LLM API — with function calling support
# ═══════════════════════════════════════════════════════════════════════════════

def _call_anthropic_with_tools(messages: list[dict], system: str, model: str,
                                api_key: str, max_tokens: int) -> dict:
    """
    Call Anthropic API with tools.
    Returns {"stop_reason": "tool_use"|"end_turn", "content": [...], "usage": {...}}
    """
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=TOOLS,
    )
    return {
        "stop_reason": resp.stop_reason,
        "content": [{"type": b.type, **({"id": b.id, "name": b.name, "input": b.input} if b.type == "tool_use" else {"text": b.text})} for b in resp.content],
        "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
    }


def _call_openai_with_tools(messages: list[dict], system: str, model: str,
                             base_url: str, api_key: str, max_tokens: int) -> dict:
    """
    Call OpenAI-compatible API with tools.
    Returns {"stop_reason": "tool_calls"|"stop", "content": [...], "usage": {...}}
    """
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key)

    # Build messages with system
    api_messages = [{"role": "system", "content": system}] + messages

    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=api_messages,
        tools=OPENAI_TOOLS,
    )

    choice = resp.choices[0]
    msg = choice.message

    # Convert to unified format
    content = []
    if msg.content:
        content.append({"type": "text", "text": msg.content})
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })

    stop_reason = "tool_calls" if msg.tool_calls else "stop"
    usage = {}
    if resp.usage:
        usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}

    return {
        "stop_reason": stop_reason,
        "content": content,
        "usage": usage,
    }


def call_llm_with_tools(messages: list[dict], system: str, provider: str,
                        model: Optional[str] = None, base_url: Optional[str] = None,
                        api_key: Optional[str] = None, max_tokens: int = 16000) -> dict:
    """
    Call LLM API with tool support. Routes to Anthropic or OpenAI based on protocol.
    Returns unified format: {stop_reason, content, usage}
    """
    from config import get_provider, resolve_api_key

    protocol, cfg = get_provider(provider)
    resolved_model = model or cfg["default_model"]
    resolved_base_url = base_url or cfg["base_url"]
    resolved_api_key = resolve_api_key(cfg, cli_key=api_key, provider_name=provider or "")

    if protocol == "anthropic":
        return _call_anthropic_with_tools(messages, system, resolved_model,
                                          resolved_api_key, max_tokens)
    else:
        return _call_openai_with_tools(messages, system, resolved_model,
                                       resolved_base_url, resolved_api_key, max_tokens)


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
                if protocol == "anthropic":
                    messages.append({"role": "assistant", "content": content})
                else:
                    messages.append({
                        "role": "assistant",
                        "content": "".join(text_parts) or None,
                    })
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
                result = _search_problem_db(
                    tool_args.get("query", ""),
                    tool_args.get("top_k", 8),
                )
                quality_state["search_attempted"] = True
                if result.get("success"):
                    quality_state["dup_verdict"] = result.get("dup_verdict")
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

            # Print result summary
            status = "✓" if result.get("success") else "✗"
            msg = result.get("message", str(result))[:150]
            print(f"    {status} {msg}")

            tool_results.append({
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "result": result,
            })

        # Add messages in protocol-specific format
        if protocol == "anthropic":
            # Anthropic format: assistant with content blocks, then user with tool_results
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tr["tool_use_id"],
                 "content": json.dumps(tr["result"], ensure_ascii=False)}
                for tr in tool_results
            ]})
        else:
            # OpenAI format: assistant with tool_calls, then separate tool messages
            messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["input"], ensure_ascii=False)}}
                    for tc in tool_parts
                ],
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": json.dumps(tr["result"], ensure_ascii=False),
                })

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

def build_user_prompt(topic: str, difficulty: int, extra: str = "",
                      test_count: int = 30, stress_iterations: int = 1000) -> str:
    """Build the user prompt for problem generation."""
    topic_desc = config.ALGO_TOPICS.get(topic, topic)
    diff_desc = config.DIFFICULTY_PRESETS.get(difficulty, f"CF {difficulty}")

    return f"""\
请生成一道算法竞赛题目，要求如下：

算法考点：{topic_desc}
难度：Codeforces {difficulty} 分（{diff_desc}）
{f'额外要求：{extra}' if extra else ''}

请根据难度自行决定数据规模、时间限制和内存限制。

语言要求：
- problem.md 必须使用中文撰写
- 标题、题目描述、输入格式、输出格式、样例、样例解释、约束和题解说明都必须是中文
- 可以保留必要的变量名、数学公式和代码块
- 如果生成了英文题面，必须先把 problem.md 完整改写/翻译为中文，再继续后续流程

请按照以下流程操作：
1. 生成所有题目文件（problem.md, solution.cpp, generator.cpp, validator.cpp, naive.cpp）
2. 编译所有 C++ 文件
3. 生成测试数据（{test_count} 组）
4. 校验输入数据
5. 运行标程生成输出
6. 对拍验证（{stress_iterations} 轮）

如果任何步骤出错，请检查错误并修复后重试。完成后总结题目信息。
"""


def generate_problem(
    topic: str,
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
) -> dict:
    """
    Full agent workflow: LLM drives the entire process via tool calling.
    """
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

    print(f"\n🤖 CP-Agent: Generating problem on '{topic}' (difficulty: {difficulty})")
    print(f"   Provider: {resolved_provider} ({protocol}), Model: {resolved_model}")
    print(f"   Output dir: {problem_dir}")

    # Build user prompt with problem dir context
    user_prompt = build_user_prompt(topic, difficulty, extra,
                                    test_count=test_count,
                                    stress_iterations=stress_iterations)
    user_prompt += f"\n\n所有文件请写入当前目录（相对路径）。文件操作的根目录已设定为：{problem_dir}"

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

    payload = {
        "success": success,
        "problem_name": problem_name,
        "topic": topic,
        "difficulty": difficulty,
        "provider": resolved_provider,
        "model": resolved_model,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - t_start, 1),
        "iterations": result.get("iterations", 0),
        "tokens": result.get("tokens", {}),
        "tool_calls": result.get("tool_stats", {}),
        "artifacts": artifacts,
        "failure_reason": failure_reason,
    }

    # Failed runs must not linger next to good problems: delete empty dirs,
    # archive non-empty ones under problems/failed/
    if success:
        write_result_json(problem_dir, payload)
    else:
        has_content = any(problem_dir.iterdir())
        if not has_content:
            problem_dir.rmdir()
            problem_dir = None
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
