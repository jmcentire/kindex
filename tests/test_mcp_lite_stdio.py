"""Exercise a fresh scoped server through a real stdio MCP client."""

import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kindex.config import Config
from kindex.store import Store


def test_fresh_home_stdio_scope_and_restart(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    outside = Store(Config(data_dir=str(home / ".kindex")))
    try:
        outside.add_node("PRIVATE_STDIO_SENTINEL", content="scopewire")
    finally:
        outside.close()
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_STATE_HOME=str(home / ".local/state"), KIN_PROFILE="unknown-home-profile",
               KIN_PROJECT_PATH=str(home), KIN_DATA_DIR=str(home / ".kindex"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "kindex.mcp_lite", "--repo", str(repo)],
        env=env, cwd=str(home),
    )

    async def request_cycle(first):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                names = {tool.name for tool in (await client.list_tools()).tools}
                assert {"search", "add", "tag_start", "task_add", "kinbase_sync"} <= names
                assert {"ingest", "remind_exec", "task_execute"}.isdisjoint(names)

                async def call(tool_name, **arguments):
                    result = await client.call_tool(tool_name, arguments)
                    return "\n".join(item.text for item in result.content if hasattr(item, "text"))

                if first:
                    result = await call("add", text="scopewire durable local memory")
                    assert "Created node:" in result, result
                    assert "Started" in await call("tag_start", name="wire-session", focus="scopewire")
                    task = await call("task_add", text="scopewire durable local task")
                    assert re.search(r"Created task: [\w:-]+", task), task
                result = await call("search", query="scopewire")
                assert "durable local memory" in result, result
                assert "PRIVATE_STDIO_SENTINEL" not in result
                assert "durable local task" in await call("task_list")
                refused = await call("search", query="scopewire", graph="global")
                assert "error" in refused.lower(), refused
                assert "PRIVATE_STDIO_SENTINEL" not in refused

    asyncio.run(request_cycle(True))
    asyncio.run(request_cycle(False))
    assert (repo / ".kin/local/kindex/kindex.db").is_file()
    assert not (home / ".config/kindex").exists()
    outside = Store(Config(data_dir=str(home / ".kindex")))
    try:
        assert outside.get_node_by_title("scopewire durable local memory") is None
        assert outside.get_node_by_title("scopewire durable local task") is None
    finally:
        outside.close()
