#!/usr/bin/env python3
"""
CP-Agent MCP Server — 把 cp-agent 暴露为 WorkBuddy / 任意 MCP 客户端的原生工具。

支持的 transport：
  - stdio           （默认，适合本机直接由客户端拉起）
  - sse             （HTTP，监听 /sse，兼容性最好，推荐给 WorkBuddy 的 url 连接器）
  - streamable-http （HTTP，监听 /mcp，现代标准）

用法：
  python mcp_server.py --transport stdio
  python mcp_server.py --transport sse   --host 0.0.0.0 --port 8000
  python mcp_server.py --transport streamable-http --host 0.0.0.0 --port 8000

环境变量：
  CP_AGENT_CONFIG   指定 config.yaml 路径（默认 <项目根>/config.yaml）
  DEEPSEEK_API_KEY  出题用的 DeepSeek key（config.yaml 里 deepseek.env_key 指向它）
  CXX               覆盖 C++ 编译器（默认 g++）
"""
import argparse
import asyncio
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

mcp = FastMCP("cp-agent-mcp")


def _sanitize(obj, depth=0):
    """把任意对象递归转成 JSON 安全的类型（Path/set/tuple 等）。"""
    if depth > 12:
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_sanitize(v, depth + 1) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _clean_result(result: dict) -> dict:
    """去掉 agent 返回的超大 messages 字段，保留可读摘要。"""
    result = dict(result or {})
    result.pop("messages", None)
    return _sanitize(result)


@mcp.tool()
async def generate_problem(
    topic: str = "",
    difficulty: int = 1500,
    idea: str = "",
    name: str = "",
    extra: str = "",
    provider: str = "",
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    max_iterations: int = 30,
    test_count: int = 30,
    stress_iterations: int = 0,
    allow_dup: bool = False,
    cross_check: bool = False,
    difficulty_review: bool = False,
) -> dict:
    """用 CP-Agent 的 LLM 流水线全自动生成一道算法竞赛题。

    自由构思模式：提供 topic（算法主题，如 dp/graph/tree）。
    题意完善模式：提供 idea（一段大致题意，系统补全成完整题目）。二者至少给一个。
    生成的产物（题面/标程/数据/校验器等）写入 problems/<name>/ 目录。
    """
    if not topic and not idea:
        return {"success": False, "message": "topic 与 idea 至少提供一个"}
    import agent

    kwargs = dict(
        topic=topic,
        difficulty=difficulty,
        idea=idea,
        problem_name=name or None,
        extra=extra,
        provider=provider or None,
        model=model or None,
        base_url=base_url or None,
        api_key=api_key or None,
        max_iterations=max_iterations,
        test_count=test_count,
        stress_iterations=stress_iterations or None,
        allow_dup=allow_dup,
        cross_check=cross_check,
        difficulty_review=difficulty_review,
    )
    result = await asyncio.to_thread(agent.generate_problem, **kwargs)
    return _clean_result(result)


@mcp.tool()
async def run_pipeline(
    problem_dir: str,
    test_count: int = 30,
    stress_iterations: int = 0,
    skip_stress: bool = False,
) -> dict:
    """对已存在的题目目录跑纯流水线（编译→生成数据→校验→求解→对拍→强度检查）。

    不需要 LLM，用于验证已有题目或重跑数据。返回各步骤结果。
    """
    import agent

    result = await asyncio.to_thread(
        agent.run_pipeline_only,
        Path(problem_dir),
        test_count,
        stress_iterations or None,
        skip_stress,
    )
    return _sanitize(result)


@mcp.tool()
async def export_problem(problem_dir: str, formats: str = "all") -> dict:
    """把生成的题目导出为评测平台数据包。

    formats: luogu（洛谷/HustOJ 扁平 zip）、hydro（Hydro 导入包）、
    polygon（Codeforces Polygon 目录），或 all（全部）。
    返回各格式的导出路径。
    """
    import export

    fmts = [f.strip() for f in formats.split(",") if f.strip()]
    if not fmts or "all" in fmts:
        fmts = list(export.FORMATS)
    result = await asyncio.to_thread(export.export_problem, Path(problem_dir), fmts)
    return _sanitize(result)


@mcp.tool()
async def list_topics() -> dict:
    """列出可用的算法主题（--topic 可填的 key 与中文说明）。"""
    import config

    return _sanitize(dict(config.ALGO_TOPICS))


@mcp.tool()
async def list_providers() -> dict:
    """列出当前配置中启用的 LLM 供应商与默认模型。"""
    import config

    return _sanitize(
        {
            "now_model": config.get_now_model(),
            "enabled": config.list_enabled_provider_choices(),
        }
    )


@mcp.tool()
async def list_difficulties() -> dict:
    """列出 Codeforces 难度档位（800-3000）与各档说明。"""
    import config

    return _sanitize(dict(config.DIFFICULTY_PRESETS))


@mcp.tool()
async def search_dedup(query: str, top_k: int = 8) -> dict:
    """在本地原题库（Codeforces + 洛谷）做 hybrid 查重检索。

    注意：需先下载 problem_db 并 `python -m problem_db build` 建立索引；
    未启用时本工具会返回 disabled=true 的提示，不影响出题主流程。
    """
    try:
        from dedup import search_problem_db

        result = await asyncio.to_thread(search_problem_db, query, top_k)
        return _sanitize(result)
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "disabled": True,
            "message": (
                f"查重库未启用或未建索引: {e}。"
                "需下载 problem_db 数据并 `python -m problem_db build` 后再用。"
            ),
        }


@mcp.tool()
async def server_info() -> dict:
    """返回 MCP 服务与 cp-agent 的基本信息（版本、项目根、题目根目录）。"""
    import config

    return _sanitize(
        {
            "server": "cp-agent-mcp",
            "project_root": str(PROJECT_ROOT),
            "problems_dir": str(config.PROBLEMS_DIR),
            "now_model": config.get_now_model(),
            "cxx": config.CXX,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CP-Agent MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="sse",
        help="stdio=本机拉起；sse/streamable-http=HTTP 服务（供 WorkBuddy url 连接器）",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
