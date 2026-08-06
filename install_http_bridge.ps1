# Install Wan2GP HTTP bridge as a Windows scheduled task.
# Run this from an elevated PowerShell (right-click → Run as Administrator).
#
# What it does:
#   - Creates a task that starts the HTTP bridge at user logon
#   - Restarts on failure (up to 3x, 1 min apart)
#   - Runs as the current user (interactive session)
#
# To uninstall:
#   Unregister-ScheduledTask -TaskName 'Wan2GP_HTTP_Bridge' -Confirm:$false
#
# Or just run scripts/install_http_bridge.ps1 — same thing.

$taskName = "Wan2GP_HTTP_Bridge"

# Skip if already installed
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$taskName' already installed. Removing old version first..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "C:\Users\franz\Wan2GP\.venv\Scripts\pythonw.exe" `
    -Argument "C:\Users\franz\Wan2GP\scripts\mcp_http_bridge.py --host 0.0.0.0 --port 9000" `
    -WorkingDirectory "C:\Users\franz\Wan2GP"

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # unlimited

$principal = New-ScheduledTaskPrincipal `
    -UserId "franz" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Wan2GP MCP HTTP bridge for remote agents. Listens on 0.0.0.0:9000. 14 tools + /outputs/{file} for direct video downloads."

Write-Host "Installed: $taskName"
Write-Host "Test now: curl http://localhost:9000/healthz"
