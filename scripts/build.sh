#!/usr/bin/env bash
# Build a single-file EzVTT for this platform.
#
# Regenerates the third-party licence file, builds with PyInstaller, runs the
# smoke test against the result, and writes a SHA-256 checksum beside it.
#
# The licence step is not optional: a bundled build redistributes its
# dependencies, which obliges it to carry their licence texts. See ADR-008.
#
#   ./scripts/build.sh              build and smoke test
#   ./scripts/build.sh --skip-smoke build only, for iterating on the spec

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

skip_smoke=0
[ "${1:-}" = "--skip-smoke" ] && skip_smoke=1

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
die()  { printf '\n  %s\n\n' "$1" >&2; exit 1; }

python="$root/.venv/bin/python"
[ -x "$python" ] || die "No .venv found. Run ./scripts/setup.sh first."

printf '\n  EzVTT build\n'

step "Installing build tools"
"$python" -m pip install --quiet --upgrade -r requirements-build.txt || die "Could not install PyInstaller."
ok "PyInstaller ready"

step "Collecting dependency licences"
"$python" scripts/gen_third_party_licenses.py || die "Licence collection failed; refusing to build."

step "Building"
rm -rf build dist
"$python" -m PyInstaller --clean --noconfirm ezvtt.spec || die "Build failed."

exe="$root/dist/ezvtt"
[ -f "$exe" ] || die "Build produced no executable."
chmod +x "$exe"
ok "dist/ezvtt  ($(du -m "$exe" | cut -f1) MB)"

if [ "$skip_smoke" -eq 0 ]; then
    step "Smoke testing the build"
    "$python" scripts/smoke_test.py "$exe" \
        || die "The build starts but does not work. Not shipping it."
fi

step "Checksum"
if command -v sha256sum >/dev/null 2>&1; then
    (cd dist && sha256sum ezvtt > ezvtt.sha256)
else
    # macOS ships shasum rather than sha256sum.
    (cd dist && shasum -a 256 ezvtt > ezvtt.sha256)
fi
ok "$(cut -d' ' -f1 dist/ezvtt.sha256)"

printf '\n  Done. Ship dist/ezvtt with LICENSE, NOTICE, and\n'
printf '  THIRD_PARTY_LICENSES.md alongside it.\n\n'
