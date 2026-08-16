<#
.SYNOPSIS
    Start the EzVTT server.

.PARAMETER Mode
    local (default), lan, hotspot, internet, or vps. See docs\RUN_MODES.md.

.PARAMETER Port
    Port to listen on. Default 8080.

.PARAMETER NoBypass
    Disable the beta login bypass.

.EXAMPLE
    .\scripts\start.ps1 -Mode lan
#>
[CmdletBinding()]
param(
    [ValidateSet('local', 'lan', 'hotspot', 'internet', 'vps')]
    [string]$Mode = 'local',

    [int]$Port = 8080,
    [string]$BindHost,
    [switch]$NoBypass,
    [switch]$NoBrowser,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host ""
    Write-Host "  EzVTT is not set up yet. Run this first:" -ForegroundColor Yellow
    Write-Host "    .\scripts\setup.ps1" -ForegroundColor White
    Write-Host ""
    exit 1
}

# Refuse to start a second instance on top of a live one -- two servers sharing
# one SQLite database is a good way to lose a session's worth of work.
$pidFile = Join-Path $root 'data\run\ezvtt.pid'
if (Test-Path $pidFile) {
    $existing = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "  EzVTT is already running (PID $existing)." -ForegroundColor Yellow
        Write-Host "  Stop it first:  .\scripts\stop.ps1" -ForegroundColor White
        Write-Host ""
        exit 1
    }
    # Stale file from a crash. Clear it and carry on rather than blocking.
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

$args = @('-m', 'ezvtt', '--mode', $Mode, '--port', $Port)
if ($BindHost)  { $args += @('--host', $BindHost) }
if ($NoBypass)  { $args += '--no-bypass' }
if ($NoBrowser) { $args += '--no-browser' }
if ($Reload)    { $args += '--reload' }

# Runs in the foreground, in this visible console, on purpose. Spawning a hidden
# background window is a well-known antivirus heuristic trigger and is not worth
# the cosmetic gain -- the GM can minimise the window.
& $venvPython @args
exit $LASTEXITCODE
