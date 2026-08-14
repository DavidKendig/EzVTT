#!/usr/bin/env bash
# Prepare EzVTT to run: virtual environment, dependencies, database.
#
# Safe to run repeatedly. Re-running upgrades dependencies and applies any new
# database migrations without touching your campaign data.
#
# Usage: ./scripts/setup.sh [--skip-assets]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_ASSETS=0
for arg in "$@"; do
    case "$arg" in
        --skip-assets) SKIP_ASSETS=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ -t 1 ]; then
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_OFF=''
fi

step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '    %s%s%s\n' "$C_GREEN" "$1" "$C_OFF"; }
warn() { printf '    %s%s%s\n' "$C_YELLOW" "$1" "$C_OFF"; }

printf '\n  EzVTT setup\n'
printf '  %s%s%s\n' "$C_DIM" "$ROOT" "$C_OFF"

# --- Python -------------------------------------------------------------------

step "Checking Python"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        ok "$("$candidate" --version 2>&1) at $(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    printf '\n  %sPython 3.10 or newer is required but was not found.%s\n' "$C_RED" "$C_OFF"
    printf '    Debian/Ubuntu  sudo apt install python3 python3-venv\n'
    printf '    Fedora         sudo dnf install python3\n'
    printf '    macOS          brew install python@3.12\n\n'
    exit 1
fi

# --- Virtual environment ------------------------------------------------------

step "Setting up the virtual environment"

VENV="$ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"

if [ -x "$VENV_PYTHON" ]; then
    ok "Reusing existing .venv"
else
    if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
        printf '\n  %sCould not create the virtual environment.%s\n' "$C_RED" "$C_OFF"
        # Debian splits venv into a separate package and the error is opaque.
        printf '  On Debian and Ubuntu you may need:  sudo apt install python3-venv\n\n'
        exit 1
    fi
    ok "Created .venv"
fi

# --- Dependencies -------------------------------------------------------------

step "Installing dependencies"

"$VENV_PYTHON" -m pip install --quiet --upgrade pip
if ! "$VENV_PYTHON" -m pip install --quiet -r "$ROOT/requirements.txt"; then
    printf '\n  %sDependency installation failed.%s\n' "$C_RED" "$C_OFF"
    printf '  If you are behind a proxy or offline, that is the usual cause.\n\n'
    exit 1
fi
ok "Dependencies installed"

# --- Database -----------------------------------------------------------------

step "Initialising the database"

"$VENV_PYTHON" -c "from ezvtt import db; a = db.initialise(); print('    ' + ('Applied: ' + ', '.join(a) if a else 'Schema already up to date'))"

# --- Assets -------------------------------------------------------------------

if [ "$SKIP_ASSETS" -eq 0 ]; then
    step "Map assets"

    count=0
    if [ -d "$ROOT/assets/bundled" ]; then
        count=$(find "$ROOT/assets/bundled" -maxdepth 1 -name '*.png' -type f 2>/dev/null | wc -l | tr -d ' ')
    fi

    if [ "$count" -gt 0 ]; then
        ok "$count asset(s) already installed"
    else
        printf '    EzVTT can install the Tom Cartos Open License Asset Bundle:\n'
        printf '    %s800 props and scenery pieces, about 537 MB.%s\n' "$C_DIM" "$C_OFF"
        printf '    %sReleased free by Tom Cartos under Tom'"'"'s Open Map License.%s\n' "$C_DIM" "$C_OFF"
        printf '    %shttps://www.tomcartos.com/toms-open-map-license%s\n\n' "$C_DIM" "$C_OFF"

        # Non-interactive shells (CI, piped installs) must not hang on a prompt.
        if [ -t 0 ]; then
            printf '    Install them now? [Y/n] '
            read -r answer
        else
            answer="n"
            warn "Non-interactive shell; skipping the asset prompt."
        fi

        case "$answer" in
            ''|[Yy]*) "$ROOT/scripts/fetch-assets.sh" ;;
            *) warn "Skipped. Run ./scripts/fetch-assets.sh later to install them." ;;
        esac
    fi
fi

# --- Done ---------------------------------------------------------------------

printf '\n  %sSetup complete.%s\n\n' "$C_GREEN" "$C_OFF"
printf '  Start EzVTT with:\n'
printf '    ./scripts/start.sh\n\n'
printf '  Other run modes (see docs/RUN_MODES.md):\n'
printf '    ./scripts/start.sh --mode lan       players join over your network\n'
printf '    ./scripts/start.sh --mode internet  reachable from the internet\n\n'
