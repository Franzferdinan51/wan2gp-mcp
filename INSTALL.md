# Wan2GP MCP Server - Install Package

Drop-in MCP server for Hermes Agent (and any MCP-compatible client)
that exposes local MiniMax H3 video generation on RTX 5060 Ti 16 GB.

## Files

- mcp_server.py - the MCP server (Python, stdio JSON-RPC, 14 tools, 3 resources)
- wan2gp-mcp.bat - Hermes launcher (Windows)
- test_mcp_smoke.py - basic tool-listing smoke test
- test_mcp_uploads.py - smoke test for upload/save tools (incl. live Telegram)

## Prerequisites

This server assumes:
- **Wan2GP cloned** to C:\Users\franz\Wan2GP\
- **MiniMax H3 weights** at D:\Wan2GP-Models\MiniMax-H3-FL2VA-pruned_int8_convrot.safetensors
- **Qwen3-VL text encoder** at D:\Wan2GP-Models\Qwen3-VL-32B-Instruct\
- **Python venv** at C:\Users\franz\Wan2GP\.venv\ with these packages:
  - mcp (>=1.0)
  - pywin32 (>=311) - needed on Windows
  - torch (>=2.10, CUDA build)
  - transformers, diffusers, gradio, mmgp, huggingface_hub, imageio, soundfile

Install missing packages:
```
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe -m pip install mcp pywin32 torch transformers diffusers gradio mmgp huggingface_hub imageio soundfile
```

For Telegram uploads, set in C:\Users\franz\AppData\Local\hermes\.env:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_HOME_CHANNEL=your_default_chat_id
```

## Install into Hermes

```
hermes config set mcp_servers.wan2gp.command 'C:\Users\franz\Wan2GP\scripts\wan2gp-mcp.bat'
hermes config set mcp_servers.wan2gp.enabled true
```

Or copy the .py/.bat files into C:\Users\franz\Wan2GP\scripts\ (already done if this came from your main box).

Restart the Hermes gateway to pick up the new MCP server.

## Smoke tests

```
# Basic tool listing
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe test_mcp_smoke.py

# Upload/save tests (saves file to Desktop, posts to httpbin, uploads to Telegram)
C:\Users\franz\Wan2GP\.venv\Scripts\python.exe test_mcp_uploads.py
```

## Tools exposed

### Generation
- h3_status — Model + current job state
- h3_generate — Submit video generation (5-15s, returns job_id)
- h3_job_status — Poll progress (step, ETA, log)
- h3_list_jobs — All jobs (current + history)
- h3_cancel_job — Kill a running job
- h3_get_default_settings — Return default H3 params

### File discovery
- h3_get_output — Resolve job_id to file paths
- h3_list_outputs — List all MP4s on disk (most recent first)

### File retrieval (small files <=50MB inline)
- h3_get_video — Get MP4 as base64 (single shot)
- h3_get_audio — Get WAV as base64

### File retrieval (large files, no cap)
- h3_get_video_chunked — Stream MP4 in base64 chunks (default 4 MB each)
- h3_save_to_path — Copy to an absolute path the agent picks

### Delivery / upload
- h3_send_to_telegram — Upload MP4 to a Telegram chat via bot API
- h3_post_to_webhook — POST MP4 to any HTTP endpoint as multipart/form-data

## Resources

- h3://status — live job state
- h3://outputs — recent output files
- h3://jobs — full job history

## Workflow for any agent

### Inline path (small files, <=50MB)
```
job_id = h3_generate(prompt="...", duration_seconds=5)
while h3_job_status(job_id).status not in ("done", "error"):
    sleep(30)
out = h3_get_output(job_id)
video = h3_get_video(out["muxed_path"])
# decode base64, write to disk / attach / re-host
```

### Direct delivery path
```
job_id = h3_generate(prompt="...", duration_seconds=5)
# ... poll ...
out = h3_get_output(job_id)
h3_send_to_telegram(out["muxed_path"], caption="here you go")
# OR
h3_post_to_webhook(out["muxed_path"], "https://my-service/upload")
# OR
h3_save_to_path(out["muxed_path"], "/path/on/agent/host/file.mp4")
```

### Large-file path (>50MB)
```
result = h3_get_video_chunked(path, chunk_size_mb=4)
# result.chunks is a list of {chunk_index, base64}
# decode each chunk and concatenate in chunk_index order
# result.sha256 lets the agent verify the reconstruction
```

## Caveats

- **One job at a time.** Single 16 GB GPU. Submitting while busy raises.
- **Generation takes 10-70 min** depending on duration. Plan accordingly.
- **Audio is babble.** H3 native speech is mouth-synced noise, not actual words.
- **Telegram 50 MB limit** on standard bot API. For larger videos, use local-bot-api
  or h3_save_to_path + a different delivery channel.
- **Webhook URL must be reachable from the box running the MCP server.**
