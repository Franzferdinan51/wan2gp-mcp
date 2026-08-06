@echo off
REM Hermes MCP launcher for Wan2GP / MiniMax H3 video generation.
REM Loads the venv, sets HF cache to D: drive, launches stdio MCP server.
REM
REM Install into Hermes config:
REM   mcp_servers:
REM     wan2gp:
REM       command: C:\Users\franz\Wan2GP\scripts\wan2gp-mcp.bat
REM       enabled: true

setlocal
set "REPO_ROOT=%~dp0.."
cd /d "%REPO_ROOT%"

REM Force HF cache to D: drive — Wan2GP models live on D:
set "HF_HOME=D:\Wan2GP-Models\.hf"

REM Unbuffered stdio
set "PYTHONUNBUFFERED=1"

REM Detect venv python
if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%REPO_ROOT%\.venv\Scripts\python.exe"
) else (
    echo ERROR: No venv python at %REPO_ROOT%\.venv\Scripts\python.exe 1>&2
    exit /b 1
)

"%PYTHON_BIN%" -m scripts.mcp_server %*
