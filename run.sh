#!/usr/bin/env bash
# EzVTT bootstrap launcher (macOS / Linux).
# Provisions a Python venv, installs Django, then launches the Django UI app
# (private, localhost) and the Java edge gateway (public, port 8080).
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
venv="$root/.venv"
build="$root/build"

# Ports (override by exporting these before launching).
export EzVTT_EDGE_PORT="${EzVTT_EDGE_PORT:-8080}"
export EzVTT_DJANGO_PORT="${EzVTT_DJANGO_PORT:-8000}"

if [ ! -d "$venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$venv"
fi
py="$venv/bin/python"

echo "Installing dependencies..."
"$py" -m pip install --quiet --upgrade pip
"$py" -m pip install --quiet -r "$root/requirements.txt"

echo "Compiling Java edge gateway..."
javac -d "$build" "$root/server/EzVTT.java"

echo "Starting Django UI app on 127.0.0.1:$EzVTT_DJANGO_PORT..."
"$py" "$root/client/manage.py" runserver "127.0.0.1:$EzVTT_DJANGO_PORT" --noreload &
django_pid=$!
trap 'kill "$django_pid" 2>/dev/null || true' EXIT

sleep 2
echo "Starting Java edge gateway on http://localhost:$EzVTT_EDGE_PORT ..."
echo "Open http://localhost:$EzVTT_EDGE_PORT/play?role=gm  and  http://localhost:$EzVTT_EDGE_PORT/play"
java -cp "$build" EzVTT
