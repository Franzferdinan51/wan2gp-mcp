"""
Wan2GP MCP HTTP Bridge
======================
Optional companion to mcp_server.py. Exposes the same 14 tools via REST
so agents that don't speak MCP stdio can still call them via HTTP.

Endpoints:
  POST /h3/status
  POST /h3/generate         body: {"prompt": "...", "duration_seconds": 5}
  POST /h3/job_status       body: {"job_id": "..."}
  POST /h3/list_jobs
  POST /h3/get_output       body: {"job_id": "..."}
  POST /h3/get_video        body: {"path": "..."}
  POST /h3/get_video_chunked body: {"path": "...", "chunk_size_mb": 4}
  POST /h3/get_audio        body: {"path": "..."}
  POST /h3/save_to_path     body: {"source_path": "...", "destination_path": "...", "overwrite": false}
  POST /h3/send_to_telegram body: {"video_path": "...", "chat_id": "...", "caption": "..."}
  POST /h3/post_to_webhook  body: {"file_path": "...", "url": "...", "form_field": "file"}
  POST /h3/cancel_job       body: {"job_id": "..."}
  POST /h3/list_outputs     body: {"limit": 50}
  POST /h3/get_default_settings
  GET  /healthz
  GET  /                     → HTML status page with all endpoints

This wraps the same JobManager + helpers used by mcp_server.py.
"""
import asyncio
import json
import sys
from pathlib import Path
from aiohttp import web

# Import the MCP server module so we share state
REPO_ROOT = Path(r"C:/Users/franz/Wan2GP")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mcp_server as core  # the actual MCP module


async def call_handler(request):
    """Map URL path to mcp_server's call_tool coroutine via asyncio.run_in_executor."""
    # URL: /h3/{tool}  →  core tool: h3_{tool}
    url_tool = request.match_info["tool"]
    tool_name = f"h3_{url_tool}"
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}

    # mcp_server.call_tool is async but doesn't actually await anything heavy,
    # so we just await it directly.
    try:
        result = await core.call_tool(tool_name, body)
    except Exception as e:
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)

    # result is a list of TextContent; concatenate text
    payload = {"tool": tool_name, "args": body}
    for item in result:
        if item.type == "text":
            try:
                payload["data"] = json.loads(item.text)
            except Exception:
                payload["text"] = item.text
    return web.json_response(payload)


async def healthz(request):
    return web.json_response({"ok": True, "service": "wan2gp-mcp-http", "tools": 14})


async def serve_output(request):
    """
    GET /outputs/{filename} → returns the MP4 file directly.

    Lets other agents (no MCP client, no shell) just GET the bytes:
        curl http://host:9000/outputs/neo_dragon_xxx.mp4 -o video.mp4

    Only files inside Wan2GP/tests/output/ are served.
    """
    name = request.match_info["filename"]
    # Security: no path traversal, only flat filenames
    if "/" in name or "\\" in name or ".." in name:
        raise web.HTTPBadRequest(reason="invalid filename")
    f = core.OUT_DIR / name
    if not f.exists() or not f.is_file():
        raise web.HTTPNotFound(reason=f"not found: {name}")
    return web.FileResponse(f)


INDEX_HTML = """<!doctype html>
<html><head><title>Wan2GP MCP HTTP Bridge</title>
<style>
body{font-family:system-ui;max-width:900px;margin:30px auto;padding:0 20px;background:#0b0b10;color:#e0e0e8}
h1{color:#7aa2f7}
table{border-collapse:collapse;width:100%;margin-top:20px}
td,th{padding:8px;text-align:left;border-bottom:1px solid #333}
code{background:#1a1a25;padding:2px 6px;border-radius:3px;color:#9ece6a}
.endpoint{color:#bb9af7}
.method{color:#7dcfff;font-weight:bold}
</style></head><body>
<h1>Wan2GP MCP HTTP Bridge</h1>
<p>REST front-end for the wan2gp MCP server. 14 tools, all POST JSON.</p>
<table>
<tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><span class="method">POST</span> <code>/h3/status</code></td><td>Model + job state</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/generate</code></td><td>Submit generation {prompt, duration_seconds, seed}</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/job_status</code></td><td>Poll job {job_id}</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/list_jobs</code></td><td>List all jobs</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/get_output</code></td><td>job_id → file paths</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/get_video</code></td><td>MP4 base64 (≤50MB)</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/get_video_chunked</code></td><td>Streamed chunks (no cap)</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/get_audio</code></td><td>WAV base64</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/save_to_path</code></td><td>Copy to absolute path</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/send_to_telegram</code></td><td>Upload to Telegram chat</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/post_to_webhook</code></td><td>POST to any URL</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/cancel_job</code></td><td>Kill running job</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/list_outputs</code></td><td>List all MP4s on disk</td></tr>
<tr><td><span class="method">POST</span> <code>/h3/get_default_settings</code></td><td>Return H3 params</td></tr>
<tr><td><span class="method">GET</span> <code>/healthz</code></td><td>Liveness probe</td></tr>
</table>
<h2>Example</h2>
<pre><code>curl -X POST http://localhost:9000/h3/list_outputs \\
  -H "Content-Type: application/json" \\
  -d '{"limit": 5}'</code></pre>
</body></html>"""


async def index(request):
    return web.Response(text=INDEX_HTML, content_type="text/html")


def build_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/outputs/{filename}", serve_output)
    app.router.add_post("/h3/{tool}", call_handler)
    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    web.run_app(build_app(), host=args.host, port=args.port)
