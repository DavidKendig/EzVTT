# EzVTT — Build Plan

The roadmap, phase by phase. Ordered so that **the app is usable at a real table
at the end of Phase 2**, then hardened outward. Core usability first; fancy
later.

Each phase is roughly one working session and ends in a state that runs and has
been verified by hand, not merely written.

**Legend:** `[ ]` not started · `[~]` in progress · `[x]` done and verified

Update `docs/PROGRESS.md` at the end of every session. Record settled
architectural choices in `docs/DECISIONS.md`.

---

## Phase 0 — Foundation `[x]`

Scaffolding, licensing, and the skeleton everything else hangs off.

- [x] Directory tree, `LICENSE` (Apache 2.0), `NOTICE`
- [x] `README.md` with acknowledgements — Tom Cartos, TOM licence link, dependency credits
- [x] `.gitignore`, `requirements.txt`
- [x] `docs/DECISIONS.md`, `CLAUDE.md`
- [x] `scripts/gen_third_party_licenses.py` (for packaged builds — ADR-008)
- [x] Virtual environment and dependencies installed and importing
- [x] `pyproject.toml`
- [x] `docs/RUN_MODES.md`, `docs/PROGRESS.md`
- [x] `assets/bundled/LICENSE-ASSETS.md`
- [x] `ezvtt/config.py` — paths, environment, run mode
- [x] `ezvtt/db.py` — connection, migration runner
- [x] `ezvtt/migrations/001_init.sql` — full initial schema
- [x] `ezvtt/app.py` — FastAPI factory, `/health`
- [x] `ezvtt/__main__.py` — argparse CLI (`--mode`, `--port`, `--host`, `--reload`)
- [x] `ezvtt/auth.py` — role skeleton, beta bypass defaulting ON
- [x] Base Jinja2 template, dark CSS design system, placeholder pages
- [x] `setup` · `start` · `stop` · `kill` · `fetch-assets` for PowerShell and POSIX
- [x] Verified end to end; stale prototype memories replaced

> Role plumbing is wired in from day one with the bypass on, so Phase 3 fills in
> real accounts rather than retrofitting permissions through finished code.

---

## Phase 1 — Maps, grid, and the live board `[x]` ← *first playable*

- [x] Map upload (drag-and-drop), rename, delete
- [x] Canvas board renderer with pan and zoom
- [x] **Grid overlay with a live pixel-width slider** — drag until it matches the art
- [x] Grid X/Y offset nudge, colour, opacity
- [x] Per-map grid settings persisted and restored
- [x] `/` GM screen · `/display` chrome-free second-monitor view
- [x] "Open Display Window" button
- [x] WebSocket hub: connect, full state resync, auto-reconnect
- [x] **Verified:** grid change reaches the display live; clamping and colour
      validation hold; anonymous callers are refused at the API and the socket;
      traversal blocked; grid restored byte-for-byte after a full restart

---

## Phase 2 — Assets and tokens `[x]` ← *usable at a table*

- [x] `fetch-assets` installs the Cartos bundle
- [x] Import parses `_NxM` from filenames for grid footprint (304 unsuffixed → 1×1)
- [x] Asset library UI: search, categories, thumbnails, paged
- [x] Upload, rename, **resize by squares occupied**, delete — display-only for bundled art (ADR-005)
- [x] Place, drag, rotate, delete tokens
- [x] Snap-to-grid toggle; Alt overrides it for scatter
- [x] Three layers — map / object / token — with z-ordering
- [x] Hide tokens from players; label them; lock them
- [x] Token positions clamped to the map so nothing is lost off-board
- [x] **Verified:** `TC_Anvil 02_2x1.png` imports as 2×1; a hidden token is
      absent from the player payload entirely; positions survive restart

---

## Phase 3 — Authentication `[x]`

- [x] First-run wizard **forces master admin creation** before any route is reachable
- [x] Login, logout, `scrypt` hashing, signed session cookies
- [x] Admin creates GM and player accounts; reset passwords, change roles, disable, delete
- [x] Role enforcement on **every** REST route and WebSocket intent, via shared
      guards in `deps.py` applied at the router
- [x] **CSRF protection** on every state-changing form (double-submit cookie)
- [x] **Beta bypass button** — one click, mints a *real* session for a real account
- [x] Bypass auto-disables in `internet` and `vps` modes; persistent banner while active
- [x] **Verified:** a wiped database funnels every route to the wizard; a player
      is refused GM routes at the API and the socket, not just in the UI;
      internet mode refuses the bypass even when POSTed directly
- [ ] Invite links — deferred; account creation covers the requirement

---

## Phase 4 — Fog of war `[x]`

- [x] Cell-aligned reveal mask, run-length encoded
- [x] GM brush: round brush, adjustable size, Shift to conceal, reveal/hide all
- [x] **Players are served a composited map, never the original** (ADR-011)
- [x] `/media/maps/` is GM-only
- [x] Tokens in unrevealed cells omitted from player payloads entirely
- [x] Display window paints fog fully opaque; GM sees it translucent
- [x] **"See what players see"** toggle on the GM screen
- [x] Fog persisted per scene, remapped when the grid changes
- [x] **Verified:** the player payload never names the map file; a player
      fetching `/media/maps/…` gets 403; a fully concealed composite is
      entirely black; revealing a corner shows only that corner

---

## Phase 5 — Chat and dice `[x]`

- [x] Chat panel on `/` and `/play`, collapsible; **absent from `/display`**
- [x] **Server-side** dice evaluation: `NdM±K`, `kh`/`kl`/`dh`/`dl`, `d%`, multiple terms
- [x] Per-die detail, dropped dice struck through, natural 1s and maxes marked
- [x] Quick-roll buttons plus `/r 2d6+3` typed notation
- [x] Public rolls broadcast; **private rolls** to roller and GMs only
- [x] GM whisper to an individual player
- [x] History persisted with a retention cap, replayed through the *same*
      visibility filter as live delivery
- [x] **Verified:** a private roll and a whisper both reach exactly two parties,
      confirmed on a third player's socket and in their replayed history; an
      injected `total` in the payload is ignored

---

## Phase 6 — Run modes and networking `[x]`

- [x] `--mode {local,lan,hotspot,internet,vps}`
- [x] `local` — bind loopback, auto-open the GM browser
- [x] `lan` — bind all interfaces, show join URL **and a scannable QR** on the
      GM screen, plus a full-screen view to turn towards the table
- [x] Alternative addresses listed when the machine has several (VPN, VM switch)
- [x] `hotspot` — best-effort host AP with actionable failure text, automatic
      fallback to the existing network
- [x] `internet` — public address lookup, forced auth, port-forward guidance,
      TLS warnings
- [x] `vps` — trusts `X-Forwarded-*`, secure cookies, nginx + systemd samples
- [x] **Refuses to start over a stale instance** rather than silently shadowing it
- [x] **Verified:** the SVG QR reproduces the encoder's module matrix exactly;
      the join API is GM-only (player 403, anonymous 401)
- [ ] Optional `ezvtt.local` via mDNS — deferred; the QR removes the need to
      type an address at all, which was the reason for wanting mDNS

---

## Phase 7 — Obsidian vault and player notes `[x]`

- [x] Configure a local vault path
- [x] Read-only Markdown rendering: `[[wikilinks]]`, `![[embeds]]`, frontmatter
- [x] Folder tree and search
- [x] Path-traversal guarded, confined to the vault root
- [x] **Folder allow-list, denying by default** — a raw vault is full of GM spoilers
- [x] **HTML sanitising** — a note pasted from a web page cannot script a player's browser
- [x] Player notes: private per-user, a GM-visible tier, and a shared board
- [x] **Verified:** ten traversal attempts refused; search never crosses the
      allow-list; a forbidden note and a nonexistent one give the *same* error
- [x] Wiki and notes panels on `/` and `/play` — a left-docked drawer mirroring
      chat, with folder tree, search, and wikilink navigation
- [x] Admin vault configuration with folder tick-boxes, where "nothing shared"
      is stated in warning colour rather than looking like a broken wiki

---

## Phase 8 — Quality of life `[~]`

- [x] Scenes — prep several encounters, switch with one click
      (several per map, rename, copy layout *and* fog, delete; clicking a map
      returns to the scene last run; scene names never reach players)
- [x] Initiative tracker — per scene, d20 rolled server-side, entries concealable,
      shown on the GM screen, the player view, and the projector
- [ ] Ruler in grid units; AoE templates (cone, circle, line)
- [ ] Alt-click **ping** visible to everyone
- [ ] **Grid auto-detect** from the map image
- [ ] Undo/redo for GM board edits
- [ ] Token HP bars and condition markers
- [ ] Handout push — show an image to players
- [ ] Campaign export/import as a zip

---

## Phase 9 — Packaging `[ ]`

- [ ] PyInstaller single-file builds for Windows, macOS, Linux
- [ ] GitHub Actions release matrix with checksums
- [ ] First-run data directory bootstrap outside the bundle
- [ ] **Run `scripts/gen_third_party_licenses.py` and ship its output** — bundled builds embed dependencies, which triggers their licence-inclusion terms (ADR-008)
- [ ] Per-platform smoke tests

---

## Phase 10 — SRD support `[ ]`

- [ ] Ingest 5e SRD 5.1 (CC BY 4.0) — statblocks, conditions, spells
- [ ] Searchable reference panel
- [ ] Drop a monster onto the board as a token with its statblock attached

---

## Deferred / considered and not scheduled

- Dynamic lighting and line-of-sight — a large win, but expensive; revisit after Phase 8
- Native desktop shell (Tauri/Electron) — decided against for now; two browser windows serve every run mode with one code path
- Soundboard and ambient audio
- Light theme as an option — dark remains the default and the design target (ADR-006)
- Docker Compose for VPS deployment
