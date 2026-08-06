"""Smoke test the Streamable HTTP MCP endpoint using the real MCP SDK client."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(r"C:/Users/franz/Wan2GP/.venv/Lib/site-packages")))

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


async def main():
    url = "http://localhost:9100/mcp"
    print(f"=== connecting to {url} ===")
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"  protocol: {init.protocolVersion}")
            print(f"  server: {init.serverInfo.name} v{init.serverInfo.version}")
            print(f"  capabilities: {list(init.capabilities.model_dump().keys())}")

            print("\n=== tools/list ===")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:60]}")

            print("\n=== tools/call h3_status ===")
            r = await session.call_tool("h3_status", {})
            print(r.content[0].text[:300])

            print("\n=== resources/list ===")
            res = await session.list_resources()
            for r in res.resources:
                print(f"  - {r.uri}")

            print("\n=== tools/call h3_list_outputs ===")
            r = await session.call_tool("h3_list_outputs", {"limit": 3})
            data = json.loads(r.content[0].text)
            print(f"  found {len(data)} outputs")
            for d in data[:3]:
                print(f"    - {d['filename']} ({d['size_mb']} MB)")


asyncio.run(main())
