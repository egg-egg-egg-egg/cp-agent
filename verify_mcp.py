#!/usr/bin/env python3
"""
本地验证脚本：用 MCP stdio 客户端拉起 mcp_server.py，确认服务能启动、工具可列出并调用。
不需要 LLM key（只验证 server_info / list_topics / list_providers 这类轻量工具）。
用法：
  .venv/Scripts/python.exe verify_mcp.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = ROOT / ".venv" / "bin" / "python"


def _text(content) -> str:
    parts = []
    for block in content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def main() -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=str(VENV_PY),
        args=[str(ROOT / "mcp_server.py"), "--transport", "stdio"],
        env={**os.environ, "CP_AGENT_CONFIG": str(ROOT / "config.yaml")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("✅ 已连接，工具列表:", names)

            for tool in ("server_info", "list_topics", "list_providers", "list_difficulties"):
                if tool in names:
                    res = await session.call_tool(tool, {})
                    print(f"\n── {tool} ──")
                    print(_text(res.content)[:800])

            # search_dedup 在未建索引时应优雅返回 disabled=true
            if "search_dedup" in names:
                res = await session.call_tool("search_dedup", {"query": "动态规划 背包", "top_k": 5})
                print("\n── search_dedup（预期 disabled=true）──")
                print(_text(res.content)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
