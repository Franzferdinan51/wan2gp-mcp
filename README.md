# Wan2GP MCP Server

Expose local [MiniMax H3](https://huggingface.co/DeepBeepMeep/MiniMax-H3) video generation on your RTX 5060 Ti (or any 16 GB+ CUDA GPU) to **any MCP-aware agent** — including Claude Desktop, Continue, Cline, Cursor, Windsurf, and Hermes Agent.

Three transports, one shared backend:

| Transport | URL | Use for |
|-----------|-----|---------|
| **stdio** | `C:\Users\franz\Wan2GP\scripts\wan2gp-mcp.bat` | Local agents (Hermes, Claude Desktop) |
| **Streamable HTTP** | `http://<host>:9100/mcp` | MCP-over-HTTP clients on the network |
| **REST bridge** | `http://<host>:9000` | Plain curl / non-MCP clients |

## Tools exposed (14)

### Generation
- `h3_status` — model + current job state
- `h3_generate` — submit generation (5-15s, returns `job_id`)
- `h3_job_status` — poll progress
- `h3_list_jobs` — all jobs
- `h3_cancel_job` — kill running job
- `h3_get_default_settings` — H3 params

### File discovery
- `h3_get_output` — `job_id` → file paths
- `h3_list_outputs` — list all MP4s on disk

### Retrieval
- `h3_get_video` — MP4 base64 (≤50 MB)
- `h3_get_audio` — WAV base64
- `h3_get_video_chunked` — streamed chunks (no cap)
- `h3_save_to_path` — copy to absolute path

### Delivery
- `h3_send_to_telegram` — upload to a Telegram chat
- `h3_post_to_webhook` — POST to any HTTP endpoint

Plus 3 resources:
- `h3://status`, `h3://outputs`, `h3://jobs`

## Install

See [INSTALL.md](INSTALL.md) for full setup. Quick version:

```bash
# Clone the Wan2GP repo and download H3 weights (one-time)
git clone https://github.com/deepbeepmeep/Wan2GP C:/Users/franz/Wan2GP
# ... download MiniMax H3 + Qwen3-VL-32B to D:\Wan2GP-Models\ ...

# Install Python deps in the Wan2GP venv
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe -m pip install mcp pywin32 aiohttp starlette uvicorn

# Copy these files into C:\Users\franz\Wan2GP\scripts\

# Register with Hermes
hermes config set mcp_servers.wan2gp.command 'C:\Users\franz\Wan2GP\scripts\wan2gp-mcp.bat'
hermes config set mcp_servers.wan2gp.enabled true
```

For Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "wan2gp": {
      "command": "C:\\Users\\franz\\Wan2GP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\franz\\Wan2GP\\scripts\\mcp_server.py"]
    }
  }
}
```

For remote agents on Tailscale, just point them at:
```
http://100.116.54.125:9100/mcp
```

## Smoke tests

```bash
# stdio + tool listing
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe test_mcp_smoke.py

# save / chunked / telegram / webhook
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe test_mcp_uploads.py

# streamable HTTP from outside the box
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe test_mcp_http.py
```

## Caveats

- **One job at a time** — single 16 GB GPU. Submitting while busy raises.
- **Generation takes 10-70 min** depending on duration.
- **Audio is babble** — H3's native speech is mouth-synced noise, not real words.
- **Telegram 50 MB cap** — use `h3_save_to_path` + custom delivery for larger files.

## Credits

- MiniMax H3 model by [DeepBeepMeep](https://huggingface.co/DeepBeepMeep/MiniMax-H3)
- Wan2GP by [DeepBeepMeep](https://github.com/deepbeepmeep/Wan2GP)
- Built for Hermes Agent by DuckHive
