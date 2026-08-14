#!/usr/bin/env bash
# Install the Tom Cartos Open License Asset Bundle.
#
# Copies the asset bundle into assets/bundled/ where EzVTT can find it. Looks for
# an already-downloaded copy on this machine first; falls back to telling you
# where to get it.
#
# The bundle is roughly 537 MB across 800 files and is deliberately not tracked
# in git -- see ADR-003.
#
# LICENCE: these assets are the work of Tom Cartos, provided under Tom's Open Map
# License, and are NOT covered by EzVTT's Apache licence. EzVTT never modifies
# them on disk. See https://www.tomcartos.com/toms-open-map-license
#
# Usage: ./scripts/fetch-assets.sh [--source DIR] [--force]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/assets/bundled"

LICENSE_URL='https://www.tomcartos.com/toms-open-map-license'
SITE_URL='https://www.tomcartos.com/'

SOURCE=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

printf '\n  Tom Cartos Open License Asset Bundle\n'
printf '  Artwork by Tom Cartos -- %s\n' "$SITE_URL"
printf '  Licence: %s\n\n' "$LICENSE_URL"

mkdir -p "$TARGET"

existing=$(find "$TARGET" -maxdepth 1 -name '*.png' -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$existing" -gt 0 ] && [ "$FORCE" -eq 0 ]; then
    printf '  %s asset(s) already installed.\n' "$existing"
    printf '  Use --force to reinstall.\n\n'
    exit 0
fi

# --- Locate a source ----------------------------------------------------------

is_bundle_dir() {
    [ -n "${1:-}" ] && [ -d "$1" ] || return 1
    find "$1" -name 'TC*.png' -type f -print -quit 2>/dev/null | grep -q .
}

if [ -z "$SOURCE" ]; then
    # Common places the bundle ends up after being downloaded and unzipped.
    for candidate in \
        "$ROOT/Tom Cartos Open License Asset Bundle" \
        "$ROOT/../Tom Cartos Open License Asset Bundle" \
        "$HOME/Downloads/TomCartosOpenVTTAssets" \
        "$HOME/Downloads/Tom Cartos Open License Asset Bundle"
    do
        if is_bundle_dir "$candidate"; then SOURCE="$candidate"; break; fi
    done
fi

if ! is_bundle_dir "$SOURCE"; then
    printf '  Could not find the asset bundle on this machine.\n\n'
    printf '  Download it free from Tom Cartos:\n'
    printf '    %s\n\n' "$SITE_URL"
    printf '  Then unzip it and point this script at the folder:\n'
    printf '    ./scripts/fetch-assets.sh --source /path/to/bundle\n\n'
    printf '  EzVTT works fine without these assets -- you can upload your own\n'
    printf '  maps and tokens instead. They are a convenience, not a requirement.\n\n'
    exit 1
fi

# --- Copy ---------------------------------------------------------------------

total=$(find "$SOURCE" -name '*.png' -type f | wc -l | tr -d ' ')
printf '  Installing %s asset(s) from:\n' "$total"
printf '    %s\n\n' "$SOURCE"

copied=0
index=0
# NUL-delimited: several bundle filenames contain spaces.
while IFS= read -r -d '' file; do
    index=$(( index + 1 ))
    destination="$TARGET/$(basename "$file")"

    if [ -e "$destination" ] && [ "$FORCE" -eq 0 ]; then
        continue
    fi
    cp -f "$file" "$destination"
    copied=$(( copied + 1 ))

    if [ $(( index % 50 )) -eq 0 ] || [ "$index" -eq "$total" ]; then
        printf '\r  %s of %s...' "$index" "$total"
    fi
done < <(find "$SOURCE" -name '*.png' -type f -print0)
printf '\r%*s\r' 40 ''

# The licence notice lives beside the artwork so it cannot be separated from it.
if [ ! -f "$TARGET/LICENSE-ASSETS.md" ]; then
    printf '  Warning: LICENSE-ASSETS.md is missing from assets/bundled/.\n'
    printf '  Restore it from the repository -- it must ship with the artwork.\n\n'
fi

printf '  Installed %s asset(s) to assets/bundled/\n\n' "$copied"
printf '  These assets are the work of Tom Cartos and remain under his licence.\n'
printf '  Please credit him and read the terms: %s\n\n' "$LICENSE_URL"
printf '  EzVTT will index them the next time it starts.\n\n'
