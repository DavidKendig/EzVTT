#!/usr/bin/env bash
# Start the EzVTT server.
#
# Usage: ./scripts/start.sh [--mode MODE] [--port N] [--host ADDR]
#                           [--no-bypass] [--no-browser] [--reload]
#
# Modes: local (default), lan, hotspot, internet, vps. See docs/RUN_MODES.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV_PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
    printf '\n  EzVTT is not set up yet. Run this first:\n'
    printf '    ./scripts/setup.sh\n\n'
    exit 1
fi

# Refuse to start a second instance on top of a live one -- two servers sharing
# one SQLite database is a good way to lose a session's worth of work.
PID_FILE="$ROOT/data/run/ezvtt.pid"
if [ -f "$PID_FILE" ]; then
    existing=$(head -n1 "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$existing" ] && kill -0 "$existing" 2>/dev/null; then
        printf '\n  EzVTT is already running (PID %s).\n' "$existing"
        printf '  Stop it first:  ./scripts/stop.sh\n\n'
        exit 1
    fi
    # Stale file from a crash. Clear it and carry on rather than blocking.
    rm -f "$PID_FILE"
fi

# Runs in the foreground on purpose. Backgrounding it would detach the server
# from the terminal that owns it and make Ctrl+C stop working.
exec "$VENV_PYTHON" -m ezvtt "$@"
