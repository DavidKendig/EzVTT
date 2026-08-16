# EzVTT — orientation for contributors and AI assistants

**Read this file and `docs/PROGRESS.md` first.** Between them they should tell you
where the project stands and what the next step is, without reading the codebase.

EzVTT is a virtual tabletop optimised for one thing: **a GM gets a map on the
table and starts playing in under a minute.** When a design question is close,
resolve it in favour of that.

---

## Rules that are not negotiable

1. **Never modify a file under `assets/bundled/`.** Tom's Open Map License does
   not permit editing those assets. Sizing is a display-time transform over the
   unmodified original; thumbnails are written to `data/thumbs/`, never in place.
   See ADR-005.
2. **The server is authoritative.** Clients send intents; the server checks the
   role, writes, then broadcasts. Fog of war and dice are worthless otherwise —
   hidden map data must never be transmitted to a player's browser. See ADR-004.
3. **No CDN, no frontend build step.** EzVTT must run with no internet
   connection. See ADR-006.
4. **The GUI defaults to dark mode.** It is used in dim rooms and projected to a
   whole table. See ADR-006.
5. **Parameterise every SQL query.** No f-strings into SQL. See ADR-002.
6. **Do not add a dependency casually.** Each one is an acknowledgement entry and
   a packaging risk. Justify it in `docs/DECISIONS.md`.
7. **Do not reproduce licence text in the README.** Link to it. See ADR-008.

---

## Layout

```
ezvtt/            application package
  config.py         paths, env, run mode
  db.py             sqlite3 connection + migration runner
  migrations/       numbered .sql, applied in order
  auth.py           scrypt hashing, signed sessions, role checks
  hub.py            WebSocket connection manager and broadcast
  dice.py           server-side dice evaluation
  grid.py           grid maths
  net.py            LAN/public IP, QR, hotspot helpers
  vault.py          Obsidian vault reader (read-only, traversal-guarded)
  media.py          upload validation, thumbnails, safe serving
  routes/           one module per feature area
  templates/        Jinja2
  static/css, static/js
scripts/          setup · start · stop · kill · fetch-assets (.ps1 + .sh each)
docs/             BUILD_PLAN · PROGRESS · DECISIONS · RUN_MODES
tests/            pytest over pure logic
data/             gitignored runtime state — db, uploads, thumbs, PID
assets/bundled/   gitignored artwork, installed by fetch-assets
```

---

## Running it

```bash
# Windows
.\scripts\setup.ps1
.\scripts\start.ps1 --mode local

# macOS / Linux
./scripts/setup.sh
./scripts/start.sh --mode local
```

Directly, against the venv:

```bash
.venv/Scripts/python -m ezvtt --mode local --reload    # Windows
.venv/bin/python -m ezvtt --mode local --reload        # macOS / Linux
```

Tests:

```bash
.venv/Scripts/python -m pytest
```

Modes are `local` · `lan` · `hotspot` · `internet` · `vps`. See
`docs/RUN_MODES.md`.

---

## Environment gotchas on this machine

These have bitten before and cost real time:

- **Do not spawn background processes with a hidden window.** PowerShell
  `-WindowStyle Hidden` around `python`/`java` trips this machine's antivirus.
  Start scripts use a visible console deliberately.
- **Git Bash mangles POSIX-looking paths** passed to native commands (MSYS path
  conversion). Prefix with `MSYS_NO_PATHCONV=1` when passing `/media/...`-style
  arguments to `curl` and friends.
- **`uvicorn --reload` caches templates in some configurations.** If an HTML
  change does not appear, restart the server rather than assuming the edit
  failed.
- **A browser that cached a static file before `Cache-Control: no-cache` was
  added will keep serving the old copy.** Static files are now sent `no-cache`,
  but an entry stored earlier keeps its old heuristic. If a JS or CSS change
  does not appear, `fetch(url, {cache: 'reload'})` once, then reload. This cost
  an hour in Phase 4 chasing correct code.

---

## Conventions

- **Naming.** Brand and identifiers use `EzVTT`. The Python package directory is
  lowercase `ezvtt` (PEP 8). Environment variables are `EZVTT_*` (shell
  convention). These are all intentional; do not "fix" them.
- **The artist is Tom Cartos**, not "Tom Carlos". The source bundle directory is
  misspelled; the code and docs are not.
- **British spelling in user-facing prose** (`licence` as a noun), American in
  code identifiers (`license_id`). Match what is already there.
- **Comments explain why, not what.** Assume the reader can read Python.

---

## Where the state lives

`data/ezvtt.db` is the single source of persistent truth: accounts, maps,
scenes, tokens, fog, notes, chat. Grid sizes, token positions, and fog are
written as they change — closing EzVTT mid-combat and reopening restores the
table exactly.

To reset: delete `data/ezvtt.db` and the first-run admin wizard reappears.
To back up a campaign: copy `data/`.

---

## Session workflow

At the **end** of a working session, update `docs/PROGRESS.md`: what shipped,
what was actually verified (not just written), and the precise next step. That
file is the handoff. Record any settled architectural choice in
`docs/DECISIONS.md` so it is not relitigated later.
