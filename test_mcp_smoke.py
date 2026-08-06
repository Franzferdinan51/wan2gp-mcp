"""Quick MCP server smoke test."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/franz/Wan2GP")))

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


async def main():
    params = StdioServerParameters(
        command=r"C:\Users\franz\Wan2GP\.venv\Scripts\python.exe",
        args=[r"C:\Users\franz\Wan2GP\scripts\mcp_server.py"],
        env={"PYTHONUNBUFFERED": "1", "HF_HOME": r"D:\Wan2GP-Models\.hf"},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== Tools ===")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:80]}")

            print("\n=== Resources ===")
            res = await session.list_resources()
            for r in res.resources:
                print(f"  - {r.uri}")

            print("\n=== h3_status ===")
            r = await session.call_tool("h3_status", {})
            print(r.content[0].text[:600])

            print("\n=== h3_list_outputs (limit 5) ===")
            r = await session.call_tool("h3_list_outputs", {"limit": 5})
            data = json.loads(r.content[0].text)
            print(f"  found {len(data)} outputs")
            for d in data[:5]:
                print(f"    - {d['filename']} ({d['size_mb']} MB)")

            print("\n=== h3_get_default_settings ===")
            r = await session.call_tool("h3_get_default_settings", {})
            print(r.content[0].text[:300])


asyncio.run(main())
