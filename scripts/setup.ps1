<#
.SYNOPSIS
    Prepare EzVTT to run: virtual environment, dependencies, database.

.DESCRIPTION
    Safe to run repeatedly. Re-running upgrades dependencies and applies any new
    database migrations without touching your campaign data.

.PARAMETER SkipAssets
    Do not offer to install the Tom Cartos map asset bundle.

.EXAMPLE
    .\scripts\setup.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipAssets
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "    $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  EzVTT setup" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

# --- Python -------------------------------------------------------------------

Write-Step "Checking Python"

$python = $null
foreach ($candidate in @('py', 'python3', 'python')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }

    # `py` needs a version argument; the others are invoked directly.
    $probe = if ($candidate -eq 'py') { @('-3', '--version') } else { @('--version') }
    $version = & $cmd.Source @probe 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -match 'Python (\d+)\.(\d+)') {
        if ([int]$Matches[1] -ge 3 -and [int]$Matches[2] -ge 10) {
            $python = @{ Exe = $cmd.Source; Args = if ($candidate -eq 'py') { @('-3') } else { @() } }
            Write-Ok "$version at $($cmd.Source)"
            break
        }
        Write-Warn "$version at $($cmd.Source) is too old (need 3.10+)"
    }
}

if (-not $python) {
    Write-Host ""
    Write-Host "  Python 3.10 or newer is required but was not found." -ForegroundColor Red
    Write-Host "  Install it from https://www.python.org/downloads/ and make sure"
    Write-Host "  you tick 'Add Python to PATH' during installation."
    Write-Host ""
    exit 1
}

# --- Virtual environment ------------------------------------------------------

Write-Step "Setting up the virtual environment"

$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Ok "Reusing existing .venv"
} else {
    & $python.Exe @($python.Args) -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "  Failed to create .venv" -ForegroundColor Red; exit 1 }
    Write-Ok "Created .venv"
}

# --- Dependencies -------------------------------------------------------------

Write-Step "Installing dependencies"

& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependency installation failed." -ForegroundColor Red
    Write-Host "  If you are behind a proxy or offline, that is the usual cause."
    exit 1
}
Write-Ok "Dependencies installed"

# --- Database -----------------------------------------------------------------

Write-Step "Initialising the database"

& $venvPython -c "from ezvtt import db; applied = db.initialise(); print('    ' + ('Applied: ' + ', '.join(applied) if applied else 'Schema already up to date'))"
if ($LASTEXITCODE -ne 0) { Write-Host "  Database initialisation failed." -ForegroundColor Red; exit 1 }

# --- Assets -------------------------------------------------------------------

if (-not $SkipAssets) {
    Write-Step "Map assets"

    $bundled = Join-Path $root 'assets\bundled'
    $count = 0
    if (Test-Path $bundled) {
        $count = @(Get-ChildItem $bundled -Filter *.png -File -ErrorAction SilentlyContinue).Count
    }

    if ($count -gt 0) {
        Write-Ok "$count asset(s) already installed"
    } else {
        Write-Host "    EzVTT can install the Tom Cartos Open License Asset Bundle:"
        Write-Host "    800 props and scenery pieces, about 537 MB." -ForegroundColor DarkGray
        Write-Host "    Released free by Tom Cartos under Tom's Open Map License." -ForegroundColor DarkGray
        Write-Host "    https://www.tomcartos.com/toms-open-map-license" -ForegroundColor DarkGray
        Write-Host ""
        $answer = Read-Host "    Install them now? [Y/n]"
        if ($answer -eq '' -or $answer -match '^[Yy]') {
            & (Join-Path $PSScriptRoot 'fetch-assets.ps1')
        } else {
            Write-Warn "Skipped. Run .\scripts\fetch-assets.ps1 later to install them."
        }
    }
}

# --- Done ---------------------------------------------------------------------

Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Start EzVTT with:"
Write-Host "    .\scripts\start.ps1" -ForegroundColor White
Write-Host ""
Write-Host "  Other run modes (see docs\RUN_MODES.md):"
Write-Host "    .\scripts\start.ps1 -Mode lan       players join over your network"
Write-Host "    .\scripts\start.ps1 -Mode internet  reachable from the internet"
Write-Host ""
