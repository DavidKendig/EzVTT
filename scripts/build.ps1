<#
.SYNOPSIS
    Build a single-file EzVTT for this platform.

.DESCRIPTION
    Regenerates the third-party licence file, builds with PyInstaller, runs the
    smoke test against the result, and writes a SHA-256 checksum beside it.

    The licence step is not optional: a bundled build redistributes its
    dependencies, which obliges it to carry their licence texts. See ADR-008.

.PARAMETER SkipSmoke
    Build without running the smoke test. For iterating on the spec only.

.EXAMPLE
    .\scripts\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    $m" -ForegroundColor Green }

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "  No .venv found. Run .\scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  EzVTT build" -ForegroundColor White

# --- Build tooling ------------------------------------------------------------

Write-Step "Installing build tools"
& $python -m pip install --quiet --upgrade -r (Join-Path $root 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { Write-Host "  Could not install PyInstaller." -ForegroundColor Red; exit 1 }
Write-Ok "PyInstaller ready"

# --- Licences -----------------------------------------------------------------

Write-Step "Collecting dependency licences"
& $python (Join-Path $root 'scripts\gen_third_party_licenses.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Licence collection failed; refusing to build." -ForegroundColor Red
    exit 1
}

# --- Build --------------------------------------------------------------------

Write-Step "Building"

# A previous run's executable can still be held open -- by a server that was
# started from it and never stopped, or by antivirus part-way through scanning
# it. Say so here rather than letting PyInstaller fail at the last step with
# "access is denied" on a file nothing appears to be using.
foreach ($stale in @((Join-Path $root 'build'), (Join-Path $root 'dist'))) {
    if (-not (Test-Path $stale)) { continue }
    try {
        Remove-Item -Recurse -Force $stale -ErrorAction Stop
    } catch {
        Write-Host "  Could not clear $stale" -ForegroundColor Red
        Write-Host "  Something is still using it. A leftover EzVTT, most likely:"
        Write-Host "    Get-Process ezvtt | Stop-Process -Force" -ForegroundColor White
        exit 1
    }
}

# PyInstaller writes its progress to stderr. Windows PowerShell wraps a native
# command's stderr in error records, which under 'Stop' aborts the script on a
# perfectly successful build the moment anyone pipes this script anywhere. The
# exit code is the truth; ask that instead.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -m PyInstaller --clean --noconfirm (Join-Path $root 'ezvtt.spec')
$buildExit = $LASTEXITCODE
$ErrorActionPreference = $previousPreference
if ($buildExit -ne 0) { Write-Host "  Build failed." -ForegroundColor Red; exit 1 }

$exe = Join-Path $root 'dist\ezvtt.exe'
if (-not (Test-Path $exe)) { Write-Host "  Build produced no executable." -ForegroundColor Red; exit 1 }
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Ok "dist\ezvtt.exe  ($size MB)"

# --- Smoke test ---------------------------------------------------------------

if (-not $SkipSmoke) {
    Write-Step "Smoke testing the build"
    & $python (Join-Path $root 'scripts\smoke_test.py') $exe
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  The build starts but does not work. Not shipping it." -ForegroundColor Red
        exit 1
    }
}

# --- Checksum -----------------------------------------------------------------

Write-Step "Checksum"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
# Written byte by byte rather than with Set-Content, which in Windows
# PowerShell adds both a UTF-8 byte-order mark and a CRLF line ending. Either
# one makes `sha256sum -c` fail: the BOM corrupts the hash, and the carriage
# return becomes part of the filename it goes looking for.
[System.IO.File]::WriteAllText("$exe.sha256", "$hash  ezvtt.exe`n",
                               (New-Object System.Text.ASCIIEncoding))
Write-Ok $hash

Write-Host ""
Write-Host "  Done. Ship dist\ezvtt.exe with LICENSE, NOTICE, and" -ForegroundColor Green
Write-Host "  THIRD_PARTY_LICENSES.md alongside it." -ForegroundColor Green
Write-Host ""
