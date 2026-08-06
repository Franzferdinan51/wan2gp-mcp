"""
Wan2GP MCP Server — Streamable HTTP transport
=============================================
Real MCP-over-HTTP. URL: http://<host>:<port>/mcp

For any MCP-aware client that supports HTTP transport (Claude Desktop,
Continue, remote Hermes, MCP CLI, etc.).
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(r"C:/Users/franz/Wan2GP")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mcp_server as core
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
import uvicorn


SESSION_MANAGER = StreamableHTTPSessionManager(core.app, stateless=True)


async def root(request):
    return JSONResponse({
        "service": "wan2gp-mcp-http",
        "mcp_endpoint": "/mcp",
        "mcp_transport": "streamable-http",
        "mcp_spec": "2025-03-26",
        "tools": 14,
        "resources": 3,
    })


async def healthz(request):
    return JSONResponse({"ok": True, "service": "wan2gp-mcp-streamable-http"})


@asynccontextmanager
async def lifespan(app):
    async with SESSION_MANAGER.run():
        yield


# Stateless mode: each request gets its own transport, no session needed.
# The /mcp endpoint delegates to handle_request() for any HTTP method.
async def mcp_route(request):
    await SESSION_MANAGER.handle_request(request.scope, request.receive, request._send)


app = Starlette(
    debug=False,
    routes=[
        Route("/", root),
        Route("/healthz", healthz),
        Route("/mcp", mcp_route, methods=["GET", "POST", "DELETE"]),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    print(f"Starting Wan2GP MCP Streamable HTTP server on http://{args.host}:{args.port}/mcp")
    print(f"  Tools: 14, Resources: 3")
    print(f"  Add to Claude Desktop / Continue / etc with URL: http://{args.host}:{args.port}/mcp")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
