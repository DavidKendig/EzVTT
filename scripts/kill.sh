#!/usr/bin/env bash
# Force-terminate the EzVTT server.
#
# Last resort, for when ./scripts/stop.sh will not work. This does NOT give the
# server a chance to close the database cleanly. SQLite is in WAL mode and will
# recover on next start, but prefer stop.sh whenever it works.
#
# Usage: ./scripts/kill.sh [--port N]

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/run/ezvtt.pid"
PORT=8080

while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

killed=()

# --- By PID file --------------------------------------------------------------

if [ -f "$PID_FILE" ]; then
    SERVER_PID=$(head -n1 "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "  Killing PID $SERVER_PID (from PID file)..."
        kill -KILL "$SERVER_PID" 2>/dev/null || true
        killed+=("$SERVER_PID")
    fi
    rm -f "$PID_FILE"
fi

# --- By port ------------------------------------------------------------------
# A crash can leave the PID file gone but the port held. Without this, the next
# start fails with "address already in use" and no obvious way to recover.

port_pids=""
if command -v lsof >/dev/null 2>&1; then
    port_pids=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)
elif command -v fuser >/dev/null 2>&1; then
    port_pids=$(fuser "$PORT/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)
elif command -v ss >/dev/null 2>&1; then
    port_pids=$(ss -lptnH "sport = :$PORT" 2>/dev/null |
                grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
fi

for candidate in $port_pids; do
    # Skip anything already handled via the PID file.
    case " ${killed[*]:-} " in *" $candidate "*) continue ;; esac

    # Only terminate something that is plausibly ours. Killing an unrelated
    # process that happens to hold the port would be a nasty surprise.
    name=$(ps -p "$candidate" -o comm= 2>/dev/null | tr -d ' ')
    case "$name" in
        python*|ezvtt*)
            echo "  Killing PID $candidate ($name) on port $PORT..."
            kill -KILL "$candidate" 2>/dev/null || true
            killed+=("$candidate")
            ;;
        *)
            echo "  Port $PORT is held by PID $candidate ($name),"
            echo "  which does not look like EzVTT. Leaving it alone."
            ;;
    esac
done

echo ""
if [ "${#killed[@]}" -gt 0 ]; then
    echo "  Terminated ${#killed[@]} process(es): ${killed[*]}"
else
    echo "  Nothing to kill -- EzVTT does not appear to be running."
fi
echo ""
