#!/usr/bin/env python3
"""MCP server exposing the plan-usage check as a tool.

Thin wrapper — all logic lives in check.py, which is also usable on its own as
a CLI. Registered as a stdio server; Claude Code spawns it per session.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from check import read_usage

mcp = FastMCP("claude-usage")


@mcp.tool(
    name="claude_usage",
    description=(
        "Read how much of the current Claude session window is used: "
        "'session_pct' (0-100) and 'resets_at' (when the 5-hour window rolls "
        "over). Use before or during long unattended runs to decide whether to "
        "keep going or stop cleanly. A 'status' other than 'ok' means usage "
        "could not be read (the browser holding the login is down or logged "
        "out); treat that as unknown usage, not as permission to continue."
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
