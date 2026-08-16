#!/usr/bin/env bash
# Stop the EzVTT server gracefully.
#
# Sends SIGTERM so in-flight requests finish and SQLite closes without leaving a
# hot journal behind. If the process does not exit within the timeout, use
# ./scripts/kill.sh.
#
# Usage: ./scripts/stop.sh [--timeout SECONDS]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/run/ezvtt.pid"
TIMEOUT=15

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout) TIMEOUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ ! -f "$PID_FILE" ]; then
    echo "  EzVTT does not appear to be running (no PID file)."
    exit 0
fi

SERVER_PID=$(head -n1 "$PID_FILE" 2>/dev/null || echo "")
if [ -z "$SERVER_PID" ]; then
    echo "  PID file is empty; removing it."
    rm -f "$PID_FILE"
    exit 0
fi

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "  No process with PID $SERVER_PID. Clearing stale PID file."
    rm -f "$PID_FILE"
    exit 0
fi

printf '  Stopping EzVTT (PID %s)...' "$SERVER_PID"

# SIGTERM, not SIGKILL: uvicorn installs a handler that runs the shutdown
# lifespan so the database closes properly. SIGKILL belongs in kill.sh.
kill -TERM "$SERVER_PID" 2>/dev/null || true

# Poll four times a second, so the deadline is expressed in ticks rather than
# seconds. Checking often keeps a fast shutdown from feeling sluggish.
ticks=0
max_ticks=$(( TIMEOUT * 4 ))
while [ "$ticks" -lt "$max_ticks" ]; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        printf ' stopped.\n'
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 0.25
    ticks=$(( ticks + 1 ))
done

printf '\n  Still running after %s seconds.\n' "$TIMEOUT"
printf '  Force it with:  ./scripts/kill.sh\n'
exit 1
