# EzVTT bootstrap launcher (Windows / PowerShell).
# Provisions a Python venv, installs Django, then launches the Django UI app
# (private, localhost) and the Java edge gateway (public, port 8080).
$ErrorActionPreference = "Stop"
$root  = $PSScriptRoot
$venv  = Join-Path $root ".venv"
$build = Join-Path $root "build"

# Ports (override by setting these env vars before launching).
if (-not $env:EzVTT_EDGE_PORT)   { $env:EzVTT_EDGE_PORT   = "8080" }
if (-not $env:EzVTT_DJANGO_PORT) { $env:EzVTT_DJANGO_PORT = "8000" }
$edgePort   = $env:EzVTT_EDGE_PORT
$djangoPort = $env:EzVTT_DJANGO_PORT

if (-not (Test-Path $venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "Installing dependencies..."
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r (Join-Path $root "requirements.txt")

Write-Host "Compiling Java edge gateway..."
javac -d $build (Join-Path $root "server\EzVTT.java")

Write-Host "Starting Django UI app on 127.0.0.1:$djangoPort..."
$django = Start-Process -FilePath $py `
    -ArgumentList @((Join-Path $root "client\manage.py"), "runserver", "127.0.0.1:$djangoPort", "--noreload") `
    -PassThru -NoNewWindow

try {
    Start-Sleep -Seconds 2
    Write-Host "Starting Java edge gateway on http://localhost:$edgePort ..."
    Write-Host "Open http://localhost:$edgePort/play?role=gm  and  http://localhost:$edgePort/play"
    java -cp $build EzVTT
} finally {
    if ($django -and -not $django.HasExited) {
        Write-Host "Stopping Django..."
        Stop-Process -Id $django.Id -Force
    }
}
