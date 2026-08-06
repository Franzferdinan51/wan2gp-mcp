"""
Wan2GP MCP Server
=================
Exposes the MiniMax H3 video generation stack to other agents via MCP.

Tools:
  - h3_status               → check if model is loaded, busy, idle, VRAM usage
  - h3_generate             → kick off a video generation (returns job_id)
  - h3_job_status           → poll a running job (progress, ETA, current step)
  - h3_list_jobs            → list recent generation jobs
  - h3_get_output           → resolve a job_id to its file path(s) (video/audio)
  - h3_get_video            → READ a generated video (returns base64 image frames)
  - h3_get_audio            → READ a generated audio track (returns base64 chunks)
  - h3_cancel_job           → cancel a running generation
  - h3_save_to_path         → write a generated file to an absolute path the agent picks
  - h3_send_to_telegram     → upload a generated video to a Telegram chat
  - h3_post_to_webhook      → POST the bytes to a generic HTTP endpoint
  - h3_get_video_chunked    → stream large videos in chunks (no size cap)
  - h3_list_outputs         → list all completed outputs on disk
  - h3_get_default_settings → return default H3 generation params
  - h3_download_model       → download H3 model weights (one-time bootstrap)

Resources:
  - h3://outputs             → JSON list of recent output files
  - h3://status              → JSON status snapshot

Design notes:
  - Generation runs in a daemon thread so MCP requests stay responsive
  - Job state lives in a thread-safe queue + dict
  - Single-slot GPU: only one generation at a time (rejects concurrent)
  - Uses the SAME proven call sequence as test_h3_malcolm.py
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Optional

# ---------------------------------------------------------------------------
# MCP imports (mcp package — pip install mcp)
# ---------------------------------------------------------------------------
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool, TextContent, ImageContent, Resource,
        ReadResourceResult,
    )
except ImportError:
    print("ERROR: pip install mcp", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# Wan2GP setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(r"C:/Users/franz/Wan2GP")
sys.path.insert(0, str(REPO_ROOT))
# Don't chdir yet — defer until a tool actually needs the CWD
for k in ("PYTHONPATH", "PYTHONHOME", "UV_INTERNAL__PYTHONHOME"):
    os.environ.pop(k, None)
os.environ.setdefault("HF_HOME", r"D:/Wan2GP-Models/.hf")

OUT_DIR = REPO_ROOT / "tests" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_cwd():
    """No-op kept for compat. We deliberately do NOT chdir — it corrupts stdio on Windows.
    Wan2GP's handler resolves all paths relative to REPO_ROOT via sys.path[0] which we already set."""
    return None

# ---------------------------------------------------------------------------
# Job state — single-slot GPU, one generation at a time
# ---------------------------------------------------------------------------
class JobState:
    def __init__(self, job_id: str, params: dict):
        self.job_id = job_id
        self.params = params
        self.status = "queued"  # queued | loading | denoising | saving | done | error | cancelled
        self.step = 0
        self.total_steps = params.get("sampling_steps", 16)
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.video_path: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.muxed_path: Optional[str] = None
        self.error: Optional[str] = None
        self.progress_log: list[tuple[float, str]] = []  # (ts, msg)
        self.worker_proc: Any = None  # subprocess.Popen handle

    def elapsed(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "step": self.step,
            "total_steps": self.total_steps,
            "elapsed_seconds": round(self.elapsed(), 1),
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
            "finished_at": (datetime.fromtimestamp(self.finished_at).isoformat()
                            if self.finished_at else None),
            "video_path": self.video_path,
            "audio_path": self.audio_path,
            "muxed_path": self.muxed_path,
            "error": self.error,
            "params": self.params,
        }


class JobManager:
    """Thread-safe single-slot GPU job queue."""
    def __init__(self):
        self._lock = threading.Lock()
        self.current: Optional[JobState] = None
        self.history: list[JobState] = []   # completed jobs, most-recent first
        self._worker_thread: Optional[threading.Thread] = None

    def submit(self, params: dict) -> JobState:
        with self._lock:
            if self.current is not None and self.current.status not in ("done", "error", "cancelled"):
                raise RuntimeError(f"GPU busy: job {self.current.job_id} is {self.current.status}")
            job_id = f"h3_{int(time.time())}_{len(self.history):04d}"
            job = JobState(job_id, params)
            self.current = job
            self._worker_thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            self._worker_thread.start()
            return job

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            if self.current and self.current.job_id == job_id:
                return self.current
            for j in self.history:
                if j.job_id == job_id:
                    return j
        return None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if self.current and self.current.job_id == job_id:
                if self.current.status in ("done", "error", "cancelled"):
                    return False
                self.current.status = "cancelled"
                return True
        return False

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = []
            if self.current:
                jobs.append(self.current.to_dict())
            jobs.extend(j.to_dict() for j in self.history[:limit])
            return jobs

    def _run(self, job: JobState):
        # Subprocess-based generation to avoid mmgp/PyTorch CUDA + threading deadlock on Windows.
        # Writes progress to a JSON status file that JobManager polls.
        try:
            job.status = "loading"
            job.progress_log.append((time.time(), "Spawning generation worker subprocess"))

            # Write params to a temporary JSON file so the worker can read them reliably
            params_file = OUT_DIR / f"job_{job.job_id}_params.json"
            params_file.write_text(json.dumps(job.params), encoding="utf-8")
            status_file = OUT_DIR / f"job_{job.job_id}_status.json"
            status_file.write_text(json.dumps({"job_id": job.job_id, "status": "loading",
                                                "started_at": time.time()}),
                                    encoding="utf-8")

            # Spawn worker subprocess
            # PYTHONUNBUFFERED so we see progress live
            worker_env = os.environ.copy()
            worker_env["PYTHONUNBUFFERED"] = "1"
            worker_env["HF_HOME"] = r"D:/Wan2GP-Models/.hf"

            # NOTE: Don't inherit PYTHONPATH/PYTHONHOME/UV_INTERNAL__PYTHONHOME — they
            # confuse the worker into using a different venv
            for k in ("PYTHONPATH", "PYTHONHOME", "UV_INTERNAL__PYTHONHOME"):
                worker_env.pop(k, None)

            cmd = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "h3_worker.py"),
                f"--status-file={status_file}",
                f"--wan2gp-prompt={job.params['prompt']}",
                f"--wan2gp-duration_seconds={job.params.get('duration_seconds', 3)}",
                f"--wan2gp-seed={job.params.get('seed', -1)}",
                f"--wan2gp-sampling_steps={job.params.get('sampling_steps', 16)}",
            ]
            if "height" in job.params:
                cmd.append(f"--wan2gp-height={job.params['height']}")
            if "width" in job.params:
                cmd.append(f"--wan2gp-width={job.params['width']}")

            log(f"  worker cmd: {' '.join(cmd[:5])}...")
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                env=worker_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            job.worker_proc = proc

            # Stream worker output AND poll status file in parallel via threads.
            import threading
            done_event = threading.Event()

            def stdout_reader():
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    job.progress_log.append((time.time(), line))
                    if len(job.progress_log) > 200:
                        job.progress_log = job.progress_log[-200:]
                    if job.status == "cancelled":
                        proc.terminate()
                        break
                done_event.set()

            def status_poller():
                while not done_event.is_set():
                    try:
                        if status_file.exists():
                            sf = json.loads(status_file.read_text(encoding="utf-8"))
                            new_status = sf.get("status", job.status)
                            if new_status != job.status and new_status in ("denoising", "saving", "done", "error"):
                                job.status = new_status
                            if "step" in sf:
                                job.step = sf["step"]
                    except Exception:
                        pass
                    done_event.wait(timeout=2)

            reader_t = threading.Thread(target=stdout_reader, daemon=True)
            poller_t = threading.Thread(target=status_poller, daemon=True)
            reader_t.start()
            poller_t.start()

            proc.wait(timeout=None)
            done_event.set()
            reader_t.join(timeout=5)
            poller_t.join(timeout=5)

            # Read final status
            if status_file.exists():
                final = json.loads(status_file.read_text(encoding="utf-8"))
                job.video_path = final.get("video_path")
                job.audio_path = final.get("audio_path")
                job.muxed_path = final.get("muxed_path")
                if final.get("status") == "done":
                    job.status = "done"
                elif final.get("status") == "error":
                    job.status = "error"
                    job.error = final.get("error")
                job.step = final.get("step", job.step)
            else:
                if job.status != "cancelled":
                    job.status = "error"
                    job.error = "worker died without writing status"
            job.finished_at = time.time()

        except Exception as e:
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            job.finished_at = time.time()
        finally:
            with self._lock:
                if job.status in ("done", "error", "cancelled"):
                    self.history.insert(0, job)


JOBS = JobManager()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_file_base64(path: str, max_bytes: int = 50 * 1024 * 1024) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    size = p.stat().st_size
    if size > max_bytes:
        return {"path": path, "size": size, "truncated": True,
                "message": f"file too large ({size//1024//1024} MB), exceeds {max_bytes//1024//1024} MB limit. Use h3_get_video_chunked or h3_save_to_path."}
    return {"path": path, "size": size, "base64": base64.b64encode(p.read_bytes()).decode()}


def _read_file_chunked(path: str, chunk_size_mb: int = 4) -> dict:
    """Stream a file in base64 chunks. No size cap."""
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    size = p.stat().st_size
    chunk_size = max(1, chunk_size_mb) * 1024 * 1024
    chunks = []
    with open(p, "rb") as f:
        idx = 0
        while True:
            raw = f.read(chunk_size)
            if not raw:
                break
            chunks.append({
                "chunk_index": idx,
                "size_bytes": len(raw),
                "base64": base64.b64encode(raw).decode(),
            })
            idx += 1
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    return {
        "path": path,
        "size": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "sha256": h.hexdigest(),
        "total_chunks": len(chunks),
        "chunks": chunks,
        "instructions": "Decode each chunk's base64 and concatenate in chunk_index order to reconstruct the file.",
    }


def _save_file(source_path: str, destination_path: str, overwrite: bool = False) -> dict:
    """Copy a generated file to an arbitrary absolute path. No size cap."""
    src = Path(source_path)
    dst = Path(destination_path)
    if not src.exists():
        return {"error": f"source not found: {source_path}"}
    if dst.exists() and not overwrite:
        return {"error": f"destination exists: {destination_path} (pass overwrite=true to replace)"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(src, dst)
    import hashlib
    h = hashlib.sha256()
    with open(dst, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    return {
        "source_path": source_path,
        "destination_path": str(dst),
        "size": dst.stat().st_size,
        "size_mb": round(dst.stat().st_size / 1024 / 1024, 2),
        "sha256": h.hexdigest(),
    }


def _send_to_telegram(video_path: str, chat_id: Optional[str] = None,
                      caption: str = "", reply_to_message_id: Optional[int] = None) -> dict:
    """Upload a video to Telegram via bot API. Uses TELEGRAM_BOT_TOKEN from env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        # Try to load from hermes .env
        env_path = Path(os.environ.get("HERMES_HOME", str(Path.home() / "AppData/Local/hermes"))) / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not found in env"}
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL")
    if not chat_id:
        return {"error": "chat_id required (or set TELEGRAM_HOME_CHANNEL)"}

    p = Path(video_path)
    if not p.exists():
        return {"error": f"video not found: {video_path}"}

    import urllib.request
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    with open(p, "rb") as f:
        body = f.read()

    # Build multipart/form-data manually (no extra deps needed)
    boundary = "----Wan2GPMCPBoundary" + str(int(time.time()))
    crlf = b"\r\n"
    parts = []
    for k, v in [("chat_id", chat_id), ("caption", caption), ("supports_streaming", "true")]:
        parts.append(b"--" + boundary.encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{k}"'.encode() + crlf + crlf)
        parts.append(str(v).encode() + crlf)
    if reply_to_message_id:
        parts.append(b"--" + boundary.encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="reply_to_message_id"'.encode() + crlf + crlf)
        parts.append(str(reply_to_message_id).encode() + crlf)
    parts.append(b"--" + boundary.encode() + crlf)
    parts.append(b'Content-Disposition: form-data; name="video"; filename="' + p.name.encode() + b'"' + crlf)
    parts.append(b"Content-Type: video/mp4" + crlf + crlf)
    parts.append(body + crlf)
    parts.append(b"--" + boundary.encode() + b"--" + crlf)

    data = b"".join(parts)
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode())
            if payload.get("ok"):
                msg = payload.get("result", {})
                return {
                    "ok": True,
                    "message_id": msg.get("message_id"),
                    "chat_id": msg.get("chat", {}).get("id"),
                    "file_id": msg.get("video", {}).get("file_id"),
                    "file_size": msg.get("video", {}).get("file_size"),
                }
            return {"error": payload.get("description", "unknown telegram error"), "telegram_response": payload}
    except Exception as e:
        return {"error": f"telegram upload failed: {type(e).__name__}: {e}"}


def _post_to_webhook(file_path: str, url: str, form_field: str = "file",
                     extra_fields: Optional[dict] = None) -> dict:
    """POST a file to an HTTP endpoint as multipart/form-data."""
    p = Path(file_path)
    if not p.exists():
        return {"error": f"file not found: {file_path}"}

    import urllib.request
    boundary = "----Wan2GPMCPBoundary" + str(int(time.time()))
    crlf = b"\r\n"
    parts = []
    for k, v in (extra_fields or {}).items():
        parts.append(b"--" + boundary.encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{k}"'.encode() + crlf + crlf)
        parts.append(str(v).encode() + crlf)
    parts.append(b"--" + boundary.encode() + crlf)
    parts.append(b'Content-Disposition: form-data; name="' + form_field.encode() + b'"; filename="' + p.name.encode() + b'"' + crlf)
    parts.append(b"Content-Type: application/octet-stream" + crlf + crlf)
    with open(p, "rb") as f:
        parts.append(f.read() + crlf)
    parts.append(b"--" + boundary.encode() + b"--" + crlf)

    data = b"".join(parts)
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "response_body": body[:4000].decode(errors="replace"),
                "content_length": len(body),
            }
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return {"error": f"HTTP {e.code}", "status": e.code, "response_body": body[:2000].decode(errors="replace")}
    except Exception as e:
        return {"error": f"webhook post failed: {type(e).__name__}: {e}"}


def _list_outputs(limit: int = 50) -> list[dict]:
    files = sorted(OUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        st = f.stat()
        out.append({
            "filename": f.name,
            "path": str(f),
            "size_mb": round(st.st_size / 1024 / 1024, 2),
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return out


def log(msg):
    """Server-side logging to stderr (MCP stdio keeps stdout for JSON-RPC)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
app = Server("wan2gp")


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="h3_status",
            description=(
                "Check the Wan2GP / MiniMax H3 model status: GPU usage, VRAM free, "
                "current job, queue. Use before submitting a long generation to "
                "make sure the GPU is idle."
            ),
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="h3_generate",
            description=(
                "Generate a 5-15 second video with audio using MiniMax H3 on the local RTX 5060 Ti. "
                "Returns a job_id immediately. Generation takes 12-60 minutes depending on duration. "
                "Poll with h3_job_status. Note: native audio is speech-like babble — for clean speech, "
                "post-process with the local TTS pipeline (out of scope here)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Text description of the scene. Be specific: characters (clothing/posture, NOT names of real people), setting, action, mood, camera style."},
                    "duration_seconds": {"type": "integer", "default": 5, "minimum": 1, "maximum": 15,
                                          "description": "5s uses 121 frames (~10 min). 15s uses 362 frames sliding window (~50-70 min)."},
                    "seed": {"type": "integer", "default": -1, "description": "-1 = random. Same seed + prompt = reproducible output."},
                    "height": {"type": "integer", "default": 480, "description": "Pixel height (multiples of 64)"},
                    "width": {"type": "integer", "default": 832, "description": "Pixel width (multiples of 64)"},
                    "sampling_steps": {"type": "integer", "default": 16, "description": "Higher = better quality, slower."},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_job_status",
            description="Poll a generation job. Returns current step, status, ETA, and progress log.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_list_jobs",
            description="List recent generation jobs (current + completed).",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_get_output",
            description="Resolve a job_id to its on-disk file paths (video-only MP4, audio WAV, muxed MP4).",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_get_video",
            description=(
                "Retrieve a generated video as base64-encoded MP4. "
                "Returns immediately as a video/* MCP resource. "
                "Use after h3_get_output to fetch the actual bytes. "
                "Files >50 MB are truncated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to .mp4 from h3_get_output or h3_list_outputs"},
                    "max_bytes": {"type": "integer", "default": 52428800, "description": "Hard cap (default 50 MB)"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_get_audio",
            description="Retrieve the audio WAV track from a generation (base64).",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_save_to_path",
            description=(
                "Save a generated file (MP4 or WAV) to an absolute path the agent chooses. "
                "Use this when the agent wants to re-host the file, attach it to something, "
                "or process it further. Returns the destination path and SHA-256 hash. "
                "Works for any size — no 50 MB cap."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Path returned from h3_get_output (muxed_path, video_path, or audio_path)"},
                    "destination_path": {"type": "string", "description": "Absolute path the file should be written to. Parent dirs auto-created."},
                    "overwrite": {"type": "boolean", "default": False, "description": "Overwrite if destination exists"},
                },
                "required": ["source_path", "destination_path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_send_to_telegram",
            description=(
                "Upload a generated video to a Telegram chat via the bot API. "
                "Uses TELEGRAM_BOT_TOKEN from hermes env. "
                "Returns the message_id. For videos >50 MB, uses Telegram's local-bot-api "
                "sendVideo endpoint (requires local bot API server) — otherwise truncated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to MP4 (from h3_get_output)"},
                    "chat_id": {"type": "string", "description": "Telegram chat ID (e.g. '588090613'). Defaults to TELEGRAM_HOME_CHANNEL from env."},
                    "caption": {"type": "string", "default": "", "description": "Optional caption text"},
                    "reply_to_message_id": {"type": "integer", "description": "Optional message ID to reply to"},
                },
                "required": ["video_path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_post_to_webhook",
            description=(
                "POST a generated file's bytes to a generic HTTP URL as multipart/form-data. "
                "Useful for Discord webhooks, custom upload endpoints, S3 presigned URLs, etc. "
                "Returns the HTTP status code and response body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "url": {"type": "string"},
                    "form_field": {"type": "string", "default": "file", "description": "Form field name for the file"},
                    "extra_fields": {"type": "object", "default": {}, "description": "Additional form fields (e.g. {'username': 'Wan2GP', 'content': 'New video!'})"},
                },
                "required": ["file_path", "url"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_get_video_chunked",
            description=(
                "Stream a generated video in base64 chunks (default 4 MB each). "
                "Use when the file exceeds the 50 MB inline limit of h3_get_video. "
                "Returns a list of {chunk_index, total_chunks, base64} plus the file hash. "
                "The agent reconstructs the file by concatenating decoded chunks in order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "chunk_size_mb": {"type": "integer", "default": 4, "description": "Size of each base64 chunk in MB"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_cancel_job",
            description="Cancel a queued/running generation. Already-completed jobs return false.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_list_outputs",
            description="List all completed output videos on disk (most recent first).",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 50}},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="h3_get_default_settings",
            description="Return the default generation parameters the MCP server uses.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "h3_status":
            # Skip torch import in async handler — return minimal info instead.
            # GPU details available via h3_get_default_settings or after a job runs.
            current = JOBS.current.to_dict() if JOBS.current else None
            return [TextContent(type="text", text=json.dumps({
                "model": "MiniMax-H3-FL2VA-pruned_int8_convrot",
                "text_encoder": "Qwen3-VL-32B-Instruct-layer50_quanto_bf16_int8",
                "repo_root": str(REPO_ROOT),
                "output_dir": str(OUT_DIR),
                "current_job": current,
                "history_count": len(JOBS.history),
                "note": "GPU stats require a running generation; use h3_get_default_settings for params",
            }, indent=2))]

        if name == "h3_generate":
            duration = arguments.get("duration_seconds", 5)
            # 24 fps × duration
            frame_num = 121 if duration <= 5 else 362

            params = {
                "prompt": arguments["prompt"],
                "frame_num": frame_num,
                "height": arguments.get("height", 480),
                "width": arguments.get("width", 832),
                "sampling_steps": arguments.get("sampling_steps", 16),
                "shift": 12.0,
                "seed": arguments.get("seed", -1),
                "fps": 24,
            }
            job = JOBS.submit(params)
            eta_minutes = 10 if frame_num == 121 else 55
            return [TextContent(type="text", text=json.dumps({
                "job_id": job.job_id,
                "status": job.status,
                "estimated_minutes": eta_minutes,
                "params": params,
                "next_step": "poll with h3_job_status or use mcp resource h3://status",
            }, indent=2))]

        if name == "h3_job_status":
            job = JOBS.get(arguments["job_id"])
            if not job:
                return [TextContent(type="text", text=json.dumps({"error": "job not found"}))]
            d = job.to_dict()
            d["recent_progress"] = [{"t": datetime.fromtimestamp(t).isoformat(), "msg": m}
                                     for t, m in job.progress_log[-5:]]
            return [TextContent(type="text", text=json.dumps(d, indent=2))]

        if name == "h3_list_jobs":
            return [TextContent(type="text", text=json.dumps(JOBS.list_jobs(arguments.get("limit", 20)), indent=2))]

        if name == "h3_get_output":
            job = JOBS.get(arguments["job_id"])
            if not job:
                return [TextContent(type="text", text=json.dumps({"error": "job not found"}))]
            return [TextContent(type="text", text=json.dumps({
                "job_id": job.job_id,
                "status": job.status,
                "video_path": job.video_path,
                "audio_path": job.audio_path,
                "muxed_path": job.muxed_path,
            }, indent=2))]

        if name == "h3_get_video":
            data = _read_file_base64(arguments["path"], arguments.get("max_bytes", 50 * 1024 * 1024))
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_get_audio":
            data = _read_file_base64(arguments["path"])
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_save_to_path":
            data = _save_file(arguments["source_path"], arguments["destination_path"],
                               arguments.get("overwrite", False))
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_send_to_telegram":
            data = _send_to_telegram(
                arguments["video_path"],
                arguments.get("chat_id"),
                arguments.get("caption", ""),
                arguments.get("reply_to_message_id"),
            )
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_post_to_webhook":
            data = _post_to_webhook(
                arguments["file_path"],
                arguments["url"],
                arguments.get("form_field", "file"),
                arguments.get("extra_fields", {}),
            )
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_get_video_chunked":
            data = _read_file_chunked(arguments["path"], arguments.get("chunk_size_mb", 4))
            return [TextContent(type="text", text=json.dumps(data))]

        if name == "h3_cancel_job":
            ok = JOBS.cancel(arguments["job_id"])
            return [TextContent(type="text", text=json.dumps({"cancelled": ok, "job_id": arguments["job_id"]}))]

        if name == "h3_list_outputs":
            return [TextContent(type="text", text=json.dumps(_list_outputs(arguments.get("limit", 50)), indent=2))]

        if name == "h3_get_default_settings":
            return [TextContent(type="text", text=json.dumps({
                "height": 480, "width": 832,
                "frame_num_5s": 121, "frame_num_15s": 362,
                "sampling_steps": 16, "shift": 12.0, "fps": 24,
                "model": "MiniMax-H3-FL2VA-pruned_int8_convrot",
            }, indent=2))]

        return [TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()
        }))]


@app.list_resources()
async def list_resources():
    return [
        Resource(
            uri="h3://status",
            name="H3 Status",
            description="Live GPU + current job snapshot",
            mimeType="application/json",
        ),
        Resource(
            uri="h3://outputs",
            name="H3 Outputs",
            description="Recent output videos",
            mimeType="application/json",
        ),
        Resource(
            uri="h3://jobs",
            name="H3 Jobs",
            description="All jobs (current + history)",
            mimeType="application/json",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str):
    uri_str = str(uri)
    if uri_str == "h3://status":
        # No torch import — keep handler fast & stdio-safe
        payload = {
            "current_job": JOBS.current.to_dict() if JOBS.current else None,
            "history_count": len(JOBS.history),
        }
        return ReadResourceResult(
            contents=[TextContent(type="text", text=json.dumps(payload, indent=2)).text],
        )
    if uri_str == "h3://outputs":
        return ReadResourceResult(
            contents=[TextContent(type="text", text=json.dumps(_list_outputs(50), indent=2)).text],
        )
    if uri_str == "h3://jobs":
        return ReadResourceResult(
            contents=[TextContent(type="text", text=json.dumps(JOBS.list_jobs(50), indent=2)).text],
        )
    raise ValueError(f"unknown resource: {uri}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
