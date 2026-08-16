# EzVTT — Packaging

How a release is built, what is in it, and what it must satisfy before it goes
out. The decisions behind this are ADR-008 (licences) and ADR-016 (the shape of
a build).

---

## What a release is

One executable per platform, with no installer and no Python required:

| Platform | Archive | Binary |
|---|---|---|
| Windows x64 | `ezvtt-<version>-windows-x64.zip` | `ezvtt.exe` |
| macOS arm64 | `ezvtt-<version>-macos-arm64.tar.gz` | `ezvtt` |
| Linux x64 | `ezvtt-<version>-linux-x64.tar.gz` | `ezvtt` |

Each archive carries `LICENSE`, `NOTICE`, `THIRD_PARTY_LICENSES.md`, and
`README.md` beside the binary, and every file is listed in `SHA256SUMS.txt` on
the release.

**Map artwork is not in it.** The Tom Cartos bundle is 537 MB and licensed
separately; `scripts/fetch-assets` installs it next to the executable, into
`assets/bundled/`. See ADR-003 and ADR-005.

---

## Building one yourself

```bash
# Windows
.\scripts\build.ps1

# macOS / Linux
./scripts/build.sh
```

That regenerates `THIRD_PARTY_LICENSES.md`, builds with PyInstaller, runs the
smoke test against the result, and writes a `.sha256` beside it. It refuses to
produce a build whose licence file is missing, and refuses to ship one that
fails the smoke test.

There is no cross-compiling: PyInstaller bundles the interpreter and libraries
of the machine it runs on, so each platform is built on that platform. The
release workflow does this on three runners.

---

## The smoke test

```bash
python scripts/smoke_test.py dist/ezvtt          # a built binary
python scripts/smoke_test.py -- python -m ezvtt  # a source checkout
```

It starts the real thing on a free port against a scratch data directory and
checks what a broken bundle actually breaks:

- `/health` answers — imports, event loop, and bootloader are intact
- `first_run` is true on an empty directory — migrations ran, the database is
  writable
- the database landed **outside** the bundle, where a campaign survives a
  restart
- `/` redirects to the setup wizard — Jinja templates are present
- `/setup` renders — a template really rendered, not just resolved
- `/static/css/ezvtt.css` is served — static files made it into the bundle
- `--licences` prints the licences carried inside the binary

A bundle that imports cleanly and cannot serve a page is the normal PyInstaller
failure, and every one of those checks is a thing that has to be listed in the
spec by hand.

---

## Where a packaged EzVTT keeps things

| | Path |
|---|---|
| Program code, templates, static files, migrations | inside the executable |
| Campaign database, uploads, thumbnails, fog composites | `data/` beside the executable |
| Map artwork | `assets/bundled/` beside the executable |

`EZVTT_DATA_ROOT` overrides where `data/` goes, frozen or not — useful for
keeping a campaign on a USB stick, and how the smoke test avoids touching
anything real.

A one-file build unpacks itself into a temporary directory on every run, which
is deleted on exit. **Nothing written there survives**, which is why the data
directory is resolved from the executable's location instead. See
`config._project_root`.

---

## Releasing

1. Update `__version__` in `ezvtt/__init__.py`.
2. Commit, then tag: `git tag v0.2.0 && git push --tags`.
3. The release workflow builds all three platforms, smoke tests each, and
   opens a **draft** release with the archives and `SHA256SUMS.txt`.
4. Read the generated notes, then publish.

`workflow_dispatch` runs the same build without publishing, for a dry run.

---

## Things that will bite

**The entry point is `scripts/entrypoint.py`, not `ezvtt/__main__.py`.**
PyInstaller runs its entry script as `__main__` with no package context, which
makes every relative import in that module fail — in the built binary only,
never in development.

**Data files are invisible to PyInstaller.** Templates, static files, and SQL
migrations are not Python and nothing imports them. They are listed in
`ezvtt.spec` by hand; anything new added under `ezvtt/` that is not a `.py`
file needs a line there.

**uvicorn and Markdown import by name at runtime.** The event loop, the HTTP
parser, the WebSocket implementation, and every Markdown extension are chosen
as strings. `collect_submodules` covers them; a dependency that does the same
trick will need the same treatment.

**Terminating a one-file build leaves a child process.** The bootloader is one
process and the program it unpacks is another. Reading the child's output to
completion after killing the parent blocks until the *child* exits, which for a
web server is never — the smoke test writes output to a file rather than a pipe
for exactly this reason.

**First launch is slow on Windows.** A 25 MB one-file binary unpacks itself and
is scanned by antivirus while it does; 20 seconds cold, a few seconds warm. The
smoke test allows 90.

**UPX is off.** Packed binaries are a reliable way to be quarantined by
antivirus, which is a much worse first impression than a larger download.
