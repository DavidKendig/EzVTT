<#
.SYNOPSIS
    Force-terminate the EzVTT server.

.DESCRIPTION
    Last resort, for when stop.ps1 will not work. This does NOT give the server a
    chance to close the database cleanly. SQLite is in WAL mode and will recover
    on next start, but prefer stop.ps1 whenever it works.

.PARAMETER Port
    Also terminate anything listening on this port, even without a PID file.
    Useful when a crashed run has left a process holding the port.
#>
[CmdletBinding()]
param(
    [int]$Port = 8080
)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root 'data\run\ezvtt.pid'

$killed = @()

# --- By PID file --------------------------------------------------------------

if (Test-Path $pidFile) {
    $serverPid = (Get-Content $pidFile | Select-Object -First 1)
    if ($serverPid -and (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
        Write-Host "  Killing PID $serverPid (from PID file)..." -ForegroundColor Yellow
        Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
        $killed += $serverPid
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# --- By port ------------------------------------------------------------------
# A crash can leave the PID file gone but the port held. Without this, the next
# start fails with "address already in use" and no obvious way to recover.

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $owner = $listener.OwningProcess
    if ($owner -and $owner -notin $killed) {
        $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if (-not $process) { continue }

        # Only terminate something that is plausibly ours. Killing an unrelated
        # process that happens to hold the port would be a nasty surprise.
        if ($process.ProcessName -match '^(python|pythonw|ezvtt)$') {
            Write-Host "  Killing PID $owner ($($process.ProcessName)) on port $Port..." -ForegroundColor Yellow
            Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
            $killed += $owner
        } else {
            Write-Host "  Port $Port is held by PID $owner ($($process.ProcessName))," -ForegroundColor Yellow
            Write-Host "  which does not look like EzVTT. Leaving it alone." -ForegroundColor Yellow
        }
    }
}

Write-Host ""
if ($killed.Count -gt 0) {
    Write-Host "  Terminated $($killed.Count) process(es): $($killed -join ', ')" -ForegroundColor Green
} else {
    Write-Host "  Nothing to kill -- EzVTT does not appear to be running." -ForegroundColor Green
}
Write-Host ""
