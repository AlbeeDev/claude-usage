#!/usr/bin/env python3
"""MCP server exposing the plan-usage check as a tool.

Thin wrapper — all logic lives in usage.py, which is also usable on its own as
a CLI. Registered as a stdio server; Claude Code spawns it per session.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    # mcp 2.x renamed FastMCP; the decorator and run() are otherwise the same.
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

from usage import read_usage

mcp = Server("claude-usage")


@mcp.tool(
    name="claude_usage",
    description=(
        "Read the user's Claude plan usage: how much of the current 5-hour "
        "limit window is spent. Returns 'session_pct' (0-100) and 'resets_at' "
        "(when the window rolls over). This is account plan usage — the meters "
        "claude.ai shows in settings — not context-window usage and not API "
        "billing. Use it when the user asks how much usage they have left or "
        "whether they are close to a limit, and before starting work long "
        "enough that hitting a limit partway through would matter. A 'status' "
        "other than 'ok' means the reading could not be taken, not that usage "
        "is low; treat it as unknown and say so rather than assuming headroom."
    ),
)
async def claude_usage() -> dict:
    # read_usage() drives Playwright's sync API, which refuses to run inside an
    # asyncio loop — so it goes to a worker thread.
    u = await asyncio.to_thread(read_usage)
    if u["status"] != "ok":
        return u
    return {
        "status": "ok",
        "session_pct": u["session_pct"],
        "resets_at": u["session_resets_at"],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
