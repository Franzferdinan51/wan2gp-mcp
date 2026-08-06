Wan2GP MCP Server for Hermes Agent
=====================================

Quick start:
1. Read INSTALL.md
2. Copy the .py/.bat files into C:\Users\franz\Wan2GP\scripts\
3. Run:  hermes config set mcp_servers.wan2gp.command 'C:\Users\franz\Wan2GP\scripts\wan2gp-mcp.bat'
4. Run:  hermes config set mcp_servers.wan2gp.enabled true
5. Restart Hermes gateway
6. Verify: python test_mcp_smoke.py
7. Verify uploads work: python test_mcp_uploads.py

Questions? Open an issue or check the wan2gp-mcp skill in Hermes.
