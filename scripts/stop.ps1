<#
.SYNOPSIS
    Stop the EzVTT server gracefully.

.DESCRIPTION
    Sends a Ctrl+Break console event to the running server, which uvicorn handles
    as a shutdown request: in-flight requests finish and SQLite closes without
    leaving a hot journal behind.

    Windows has no SIGTERM for console processes. `taskkill` without /F posts
    WM_CLOSE, which a console application never receives, and /F is an
    unconditional terminate with no chance to close the database. A console
    control event is the only graceful option -- it is exactly what pressing
    Ctrl+Break in the server's own window does.

    Sending one requires detaching from this console and attaching to the
    server's, which would leave THIS shell without a console if anything went
    wrong mid-sequence. So the attach/signal/detach happens in a short-lived
    child process instead, and this script only interprets its exit code.

    If the process does not exit within the timeout, use kill.ps1.

.PARAMETER TimeoutSeconds
    How long to wait for a clean exit. Default 15.
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 15
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root 'data\run\ezvtt.pid'

if (-not (Test-Path $pidFile)) {
    Write-Host "  EzVTT does not appear to be running (no PID file)." -ForegroundColor Yellow
    exit 0
}

$serverPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $serverPid) {
    Write-Host "  PID file is empty; removing it." -ForegroundColor Yellow
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

if (-not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
    Write-Host "  No process with PID $serverPid. Clearing stale PID file." -ForegroundColor Yellow
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

Write-Host "  Stopping EzVTT (PID $serverPid)..." -NoNewline

# Runs in the child. Exits 0 if the event was delivered, 1 otherwise.
$signaller = @'
param([int]$TargetPid)
Add-Type -Namespace EzVTT -Name Sig -MemberDefinition @"
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint p);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleCtrlHandler(IntPtr h, bool a);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool GenerateConsoleCtrlEvent(uint e, uint g);
"@
[void][EzVTT.Sig]::FreeConsole()
if (-not [EzVTT.Sig]::AttachConsole([uint32]$TargetPid)) { exit 1 }
# The event reaches every process on that console, this child included.
# Ignoring it here keeps the child alive long enough to report success.
[void][EzVTT.Sig]::SetConsoleCtrlHandler([IntPtr]::Zero, $true)
$ok = [EzVTT.Sig]::GenerateConsoleCtrlEvent(1, 0)   # 1 = CTRL_BREAK_EVENT
[void][EzVTT.Sig]::FreeConsole()
if ($ok) { exit 0 } else { exit 1 }
'@

$scriptFile = Join-Path ([System.IO.Path]::GetTempPath()) "ezvtt-stop-$PID.ps1"
Set-Content -Path $scriptFile -Value $signaller -Encoding UTF8

try {
    # -NoNewWindow so no hidden window is created: hidden background windows are
    # a well-known antivirus heuristic trigger. The child detaches from this
    # shared console itself, leaving ours intact.
    $child = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', $scriptFile, '-TargetPid', $serverPid) `
        -NoNewWindow -Wait -PassThru
    $signalled = ($child.ExitCode -eq 0)
} catch {
    $signalled = $false
} finally {
    Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue
}

if (-not $signalled) {
    Write-Host ""
    Write-Host "  Could not send a shutdown signal to PID $serverPid." -ForegroundColor Yellow
    Write-Host "  This happens when the server was started without a console of its" -ForegroundColor Yellow
    Write-Host "  own, for example from a service wrapper or a detached launcher." -ForegroundColor Yellow
    Write-Host "  Force it with:  .\scripts\kill.ps1" -ForegroundColor White
    exit 1
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
        Write-Host " stopped." -ForegroundColor Green
        # The server removes this itself on a clean exit; clear it here too in
        # case it was terminated before its shutdown handler ran.
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        exit 0
    }
    Start-Sleep -Milliseconds 250
}

Write-Host ""
Write-Host "  Still running after $TimeoutSeconds seconds." -ForegroundColor Yellow
Write-Host "  Force it with:  .\scripts\kill.ps1" -ForegroundColor White
exit 1
