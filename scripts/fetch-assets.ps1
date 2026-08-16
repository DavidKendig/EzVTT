<#
.SYNOPSIS
    Install the Tom Cartos Open License Asset Bundle.

.DESCRIPTION
    Copies the asset bundle into assets\bundled\ where EzVTT can find it. Looks
    for an already-downloaded copy on this machine first; falls back to telling
    you where to get it.

    The bundle is roughly 537 MB across 800 files and is deliberately not tracked
    in git -- see ADR-003.

    LICENCE: these assets are the work of Tom Cartos, provided under Tom's Open
    Map License, and are NOT covered by EzVTT's Apache licence. EzVTT never
    modifies them on disk. See https://www.tomcartos.com/toms-open-map-license

.PARAMETER Source
    Path to an unpacked bundle directory to install from.

.PARAMETER Force
    Reinstall even if assets are already present.
#>
[CmdletBinding()]
param(
    [string]$Source,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root 'assets\bundled'

$LICENSE_URL = 'https://www.tomcartos.com/toms-open-map-license'
$SITE_URL    = 'https://www.tomcartos.com/'

Write-Host ""
Write-Host "  Tom Cartos Open License Asset Bundle" -ForegroundColor White
Write-Host "  Artwork by Tom Cartos -- $SITE_URL" -ForegroundColor DarkGray
Write-Host "  Licence: $LICENSE_URL" -ForegroundColor DarkGray
Write-Host ""

New-Item -ItemType Directory -Path $target -Force | Out-Null

$existing = @(Get-ChildItem $target -Filter *.png -File -ErrorAction SilentlyContinue).Count
if ($existing -gt 0 -and -not $Force) {
    Write-Host "  $existing asset(s) already installed." -ForegroundColor Green
    Write-Host "  Use -Force to reinstall." -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

# --- Locate a source ----------------------------------------------------------

function Test-BundleDir {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    return @(Get-ChildItem $Path -Filter 'TC*.png' -File -Recurse -ErrorAction SilentlyContinue |
             Select-Object -First 1).Count -gt 0
}

if (-not $Source) {
    # Common places the bundle ends up after being downloaded and unzipped.
    $candidates = @(
        (Join-Path $root 'Tom Cartos Open License Asset Bundle'),
        (Join-Path $root '..\Tom Cartos Open License Asset Bundle'),
        (Join-Path $HOME 'Downloads\TomCartosOpenVTTAssets'),
        (Join-Path $HOME 'Downloads\Tom Cartos Open License Asset Bundle')
    )
    foreach ($candidate in $candidates) {
        if (Test-BundleDir $candidate) { $Source = $candidate; break }
    }
}

if (-not (Test-BundleDir $Source)) {
    Write-Host "  Could not find the asset bundle on this machine." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Download it free from Tom Cartos:"
    Write-Host "    $SITE_URL" -ForegroundColor White
    Write-Host ""
    Write-Host "  Then unzip it and point this script at the folder:"
    Write-Host "    .\scripts\fetch-assets.ps1 -Source 'C:\path\to\bundle'" -ForegroundColor White
    Write-Host ""
    Write-Host "  EzVTT works fine without these assets -- you can upload your own"
    Write-Host "  maps and tokens instead. They are a convenience, not a requirement."
    Write-Host ""
    exit 1
}

# --- Copy ---------------------------------------------------------------------

$files = @(Get-ChildItem $Source -Filter '*.png' -File -Recurse)
Write-Host "  Installing $($files.Count) asset(s) from:" -ForegroundColor Cyan
Write-Host "    $Source" -ForegroundColor DarkGray
Write-Host ""

$copied = 0
$index = 0
foreach ($file in $files) {
    $index++
    if ($index % 50 -eq 0 -or $index -eq $files.Count) {
        $percent = [math]::Round(($index / $files.Count) * 100)
        Write-Progress -Activity "Installing map assets" `
                       -Status "$index of $($files.Count)" -PercentComplete $percent
    }

    $destination = Join-Path $target $file.Name
    if ((Test-Path $destination) -and -not $Force) { continue }
    Copy-Item $file.FullName $destination -Force
    $copied++
}
Write-Progress -Activity "Installing map assets" -Completed

# The licence notice lives beside the artwork so it cannot be separated from it.
$notice = Join-Path $target 'LICENSE-ASSETS.md'
if (-not (Test-Path $notice)) {
    Write-Host "  Warning: LICENSE-ASSETS.md is missing from assets\bundled\." -ForegroundColor Yellow
    Write-Host "  Restore it from the repository -- it must ship with the artwork." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Installed $copied asset(s) to assets\bundled\" -ForegroundColor Green
Write-Host ""
Write-Host "  These assets are the work of Tom Cartos and remain under his licence." -ForegroundColor DarkGray
Write-Host "  Please credit him and read the terms: $LICENSE_URL" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  EzVTT will index them the next time it starts." -ForegroundColor DarkGray
Write-Host ""
