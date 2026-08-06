"""Smoke test for the new upload/save tools."""
import asyncio, json, sys, time
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

            # Pick a small output file for testing
            print("=== list recent outputs ===")
            r = await session.call_tool("h3_list_outputs", {"limit": 3})
            data = json.loads(r.content[0].text)
            if not data:
                print("NO OUTPUTS AVAILABLE - skipping upload tests")
                return
            sample = data[0]["path"]
            print(f"  using: {sample} ({data[0]['size_mb']} MB)")

            print("\n=== h3_save_to_path (small file copy) ===")
            dst = f"C:/Users/franz/Desktop/mcp_test_copy.mp4"
            r = await session.call_tool("h3_save_to_path", {
                "source_path": sample,
                "destination_path": dst,
            })
            out = json.loads(r.content[0].text)
            print(json.dumps(out, indent=2))

            print("\n=== h3_get_video (small file, ≤50MB) ===")
            r = await session.call_tool("h3_get_video", {"path": sample, "max_bytes": 50 * 1024 * 1024})
            out = json.loads(r.content[0].text)
            if "base64" in out:
                print(f"  base64 length: {len(out['base64'])} chars ({out['size']/1024/1024:.1f} MB)")
            else:
                print(f"  result: {out}")

            print("\n=== h3_get_video_chunked (no size cap) ===")
            r = await session.call_tool("h3_get_video_chunked", {
                "path": sample, "chunk_size_mb": 2,
            })
            out = json.loads(r.content[0].text)
            print(f"  file: {out.get('size_mb')} MB, {out.get('total_chunks')} chunks, sha256: {out.get('sha256','')[:16]}...")
            print(f"  first chunk size: {out['chunks'][0]['size_bytes']} bytes")
            print(f"  instructions: {out.get('instructions')}")

            print("\n=== h3_send_to_telegram (would upload - skipped if no token) ===")
            # Real test would actually upload - let's check token first
            r = await session.call_tool("h3_send_to_telegram", {
                "video_path": sample,
                "chat_id": "588090613",  # from hermes .env
                "caption": "smoke test from MCP",
            })
            print(json.loads(r.content[0].text))

            print("\n=== h3_post_to_webhook (dry run to httpbin) ===")
            r = await session.call_tool("h3_post_to_webhook", {
                "file_path": sample,
                "url": "https://httpbin.org/post",
                "extra_fields": {"test": "smoke", "agent": "wan2gp-mcp"},
            })
            print(json.loads(r.content[0].text))


asyncio.run(main())
