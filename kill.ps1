# kill.ps1 — stop the EzVTT servers this project started.
#
# Stops the Java edge gateway and the Django UI server, scoped to THIS project
# folder (matched by command-line path) so it will NOT touch unrelated java /
# python processes on the machine. Safe to run when nothing is up.
#
# Usage:  ./kill.ps1
$ErrorActionPreference = "Stop"
$root  = $PSScriptRoot
$build = Join-Path $root "build"
$venv  = Join-Path $root ".venv"

$stopped = 0

function Stop-Procs {
    param(
        [string]$ProcessName,   # e.g. java.exe / python.exe
        [string[]]$MustContain, # all of these substrings must appear in the command line
        [string]$Label
    )
    Get-CimInstance Win32_Process -Filter "Name='$ProcessName'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cl = $_.CommandLine
            if (-not $cl) { return $false }
            foreach ($needle in $MustContain) { if ($cl -notlike "*$needle*") { return $false } }
            return $true
        } |
        ForEach-Object {
            Write-Host ("Stopping {0,-20} PID {1}" -f $Label, $_.ProcessId)
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                $script:stopped++
            } catch {
                Write-Warning ("Could not stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message)
            }
        }
}

Write-Host "Stopping EzVTT servers for: $root"

# Java edge gateway: java launched with this project's build dir on the classpath.
Stop-Procs -ProcessName "java.exe"   -MustContain @($build)              -Label "Java gateway"

# Django UI: python from this project's venv running manage.py.
Stop-Procs -ProcessName "python.exe" -MustContain @($venv, "manage.py")  -Label "Django server"

if ($stopped -eq 0) {
    Write-Host "No EzVTT server processes were running."
} else {
    Write-Host ("Done - stopped {0} process(es)." -f $stopped)
}
