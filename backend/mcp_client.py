"""The backend's route to the MCP server.

Reads mcp.json, resolves its project-relative paths, and opens a connection
per call (spawning mcp_server/server.py over stdio).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

PROJECT_DIR = Path(__file__).resolve().parent.parent
MCP_CONFIG = PROJECT_DIR / "mcp.json"


def _resolve(value: str) -> str:
    """Turn a project-relative path in mcp.json into an absolute one."""
    candidate = PROJECT_DIR / value
    return str(candidate) if not value.startswith("/") and candidate.exists() else value


def _python(command: str) -> str:
    """mcp.json names the project's .venv python. Where there is no .venv (on
    Render the dependencies are installed into the service's own Python), run
    the MCP server with the same interpreter as the backend."""
    if command.endswith("python") and not Path(command).exists():
        return sys.executable
    return command


def _config() -> dict:
    if not MCP_CONFIG.exists():
        raise RuntimeError(f"MCP config not found: {MCP_CONFIG}")
    raw = json.loads(MCP_CONFIG.read_text())
    for entry in raw.get("mcpServers", {}).values():
        if "command" in entry:
            entry["command"] = _python(_resolve(entry["command"]))
        entry["args"] = [_resolve(a) for a in entry.get("args", [])]
        entry.setdefault("cwd", str(PROJECT_DIR))
    return raw


# "stdio" (default): the MCP server runs as its own process, started from mcp.json.
# "inprocess": the same FastMCP server object, served over FastMCP's in-memory
# transport inside the backend process. Same server code, same tools, same
# no-future checks; no extra processes. Used on Render, whose free instance has
# 512 MB, where a separate server per agent run ran it out of memory.
TRANSPORT = os.getenv("PITWALL_MCP_TRANSPORT", "stdio").lower()


def inprocess_server():
    """The pitwall FastMCP server object, imported from mcp_server/server.py."""
    sys.path.insert(0, str(PROJECT_DIR / "mcp_server"))
    import server as pitwall_mcp_server  # noqa: E402  (mcp_server/server.py)
    return pitwall_mcp_server.mcp


def target():
    """What an MCP client connects to: the in-process server, or the mcp.json config."""
    return inprocess_server() if TRANSPORT == "inprocess" else _config()


def server_command() -> tuple[str, list[str]]:
    """The pitwall server's command and args, for agents that spawn their own."""
    entry = _config()["mcpServers"]["pitwall"]
    return entry["command"], entry["args"]


class Persistent:
    """One MCP connection held open for the backend's own frequent reads (the
    timing tower, decision triggers), so each poll doesn't spawn a process."""

    def __init__(self) -> None:
        self.client: Client | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.client = Client(target())
        await self.client.__aenter__()

    async def close(self) -> None:
        if self.client:
            await self.client.__aexit__(None, None, None)
            self.client = None

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            if self.client is None:
                await self.open()
            result = await self.client.call_tool(tool, args or {})
            return result.data


shared = Persistent()


async def call(tool: str, args: dict[str, Any] | None = None) -> Any:
    """Call one MCP tool over the shared connection and return its parsed result."""
    return await shared.call(tool, args)
