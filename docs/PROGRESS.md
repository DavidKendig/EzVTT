# Progress

The handoff file. Updated at the end of every working session: what shipped,
what was actually **verified** (not merely written), and the precise next step.

Read this and `CLAUDE.md` at the start of a session and you should be oriented
without reading the codebase.

---

## Start here

**State at 2026-08-13.** Phases 0–7 done. Phases 8–10 remain.

| | |
|---|---|
| Branch | `rewrite/fastapi-vtt`, everything committed and pushed |
| PR | [#1](https://github.com/DavidKendig/EzVTT/pull/1) — 3 commits, open, not merged |
| `main` | still the original Java/Django prototype; the PR replaces it |
| Tests | **450**, all passing |
| Lint | `ruff check .` clean |

```bash
.\scripts\setup.ps1                       # once
.\start.bat                               # or: .\scripts\start.ps1 --mode lan
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
```

**What works end to end:** maps with a live grid slider · 800 Cartos assets with
footprints parsed from filenames · tokens on three layers · accounts with a
forced first-run admin · fog that removes concealed pixels server-side · chat
with server-rolled dice and private rolls · join-by-QR · a campaign wiki that
shares nothing until you tick a folder.

**Next:** Phase 8, quality of life — see the bottom of this file.

**Two things a cold session should not "fix":**

1. `codex.js` sets `innerHTML` from `/api/vault/note` on purpose. That HTML is
   sanitised server-side through a tag allow-list; escaping it again shows every
   note as raw markup.
2. `scripts/stop.ps1` sends a console control event from a child process rather
   than calling `taskkill`. Windows has no SIGTERM for console apps, and doing
   the console dance inline breaks the calling shell.

---

## Session 6 — 2026-08-13 · Phase 5 complete · **chat and dice**

**Where the project stands:** six phases done. A GM can put a map down, line up
the grid, furnish it from 800 pieces, conceal it, hand out accounts, and run the
session with dice the table can trust. Phases 0–5 of the plan are finished.

### Also added this session

`start.bat` and `stop.bat` at the top level of the project, for double-clicking
from Explorer. They anchor to their own folder (so "Run as administrator", which
starts in `system32`, still works), pass arguments through to the PowerShell
scripts, and hold the window open on failure only when double-clicked — detected
via `%cmdcmdline%`, so running them from a terminal stays quiet.

### Shipped

- **`ezvtt/dice.py`** — notation parser and evaluator. `NdM±K`, several terms,
  `kh`/`kl`/`dh`/`dl`, `d%`. Bounded at 100 dice, 1000 sides, 20 terms.
  Uses `secrets`, not `random`.
- **`ezvtt/chat.py`** — persistence, retention cap, and the visibility rules
- Hub intents `chat.say` / `chat.roll` / `chat.whisper` / `chat.history`,
  open to any **signed-in** person rather than GM-only
- **`static/js/chat.js`** + `_chat.html` — collapsible panel, quick-roll
  buttons, advantage/disadvantage, `/r 2d6+3`, private toggle, whisper roster

### The design point worth remembering

`chat.visible_to()` is used for **both** live delivery and history replay. If
those two ever disagree, a private roll a player did not see live turns up in
their log after a reconnect — which is a silent, total failure of the feature.
One function, two call sites, and a test that asserts every message in a
replayed history would also have been delivered live.

Audience rules:

| | who sees it |
|---|---|
| public chat or roll | everyone except `/display` |
| **private roll** | the roller and the GMs |
| **whisper** | the sender and the recipient — *not* other GMs |

A GM sees private rolls because they are the one who asked for the check.
Reading other people's whispers is a different thing, and they cannot.

### Verified, not just written

**A 19-check scripted pass** (`scratchpad/chat_check.py`) with four sockets — a
GM, the roller, a control player, and the display window — all passing:

- Public roll: GM, roller, and control all see it; **the display window does not**
- Private roll: GM and roller see it; **control sees nothing**
- Whisper: both parties see it; **control sees nothing**
- **A payload carrying `total: 20`, `result: 20`, and `roll: {total: 20}` was
  ignored** — the server rolled 17 and stored the notation, not the claim
- The control player's *replayed history* also contains no private roll and no
  whisper, while still containing the public rolls
- Bad notation is answered to the roller only, never broadcast
- An anonymous socket is refused: *"Sign in to do that."*

**Through the real UI:** the `adv` button rolled `2d20kh1` → 17 with the dropped
2 struck through; `/r 4d6dl1` → 17 with the dropped 2 shown; plain chat posted.

**354 tests passing** — accounts 40, assets 35, auth 14, chat 21, config 18,
db 23, dice 46, fog 39, grid 25, media 32, state 31, tokens 30. The dice tests
include a distribution check (4000 d20s must average between 9.5 and 11.5) and
a check that every face of a d6 actually appears.

---

## Session 8 — 2026-08-13 · Phase 7 complete · **the vault, deny by default**

**Where the project stands:** eight phases. The campaign wiki and player notes
are usable from both the GM screen and the player view, and the vault's
containment is verified end to end.

### The panels

A left-docked drawer mirroring chat on the right, with **Wiki** and **Notes**
tabs, shared by `/` and `/play` and absent from `/display`. It starts collapsed
and loads the tree on first open — a GM who never opens the wiki should not pay
for walking their vault.

Confirmed in the browser as both audiences:

| | GM | player-one |
|---|---|---|
| folders | GM Only, Handouts, Lore | **Handouts, Lore** |
| notes | 5 including *Secret Root Note* | **3** |
| search "Marcus" | hits | **none** |
| panel text mentions "GM Only" | — | **no** |

Notes render as real elements, not escaped markup, and `[[wikilinks]]` navigate
without a page load. The admin screen states *"Players can currently see
nothing"* in warning colour when the allow-list is empty, so a GM who ticks
nothing understands that is the design rather than a broken wiki.

**One thing the client must keep doing:** set `innerHTML` from
`/api/vault/note`, not `textContent`. The HTML is sanitised server-side through
a tag allow-list before it is sent; escaping it again would show every note as
its own markup.

### The rule the whole feature rests on

A campaign vault holds the players' handouts and the GM's plot outline in the
same folder tree, often under names like *"Session 12 - the traitor is
Marcus.md"*. So: **nothing is visible until the GM ticks a folder.** Pointing
EzVTT at a vault shares none of it.

Three independent guards, because any one failing alone should not leak:

1. `media.resolve_within` confines every path to the vault root
2. The **resolved** path is re-checked against the allow-list, so a symlink that
   escapes the root or points at an unlisted folder is refused even though it
   resolved cleanly
3. Rendered HTML is sanitised through a tag/attribute allow-list

Two smaller decisions that matter as much:

- **A forbidden note and a nonexistent one give the identical error.** Saying
  "forbidden" for one and "not found" for the other tells a player exactly which
  secrets exist to go looking for.
- **A wikilink to a note the reader may not see renders as plain text**, not a
  dead link — otherwise a player maps the GM's folder names by collecting broken
  references.

### Shipped

- **`ezvtt/vault.py`** — config, allow-list, tree, search, Markdown rendering
  with wikilinks/embeds/frontmatter, and an HTML sanitiser
- **`ezvtt/notes.py`** — private / GM-visible / public notes
- **`ezvtt/routes/wiki.py`** — `/api/vault/*` and `/api/notes/*`

### Verified

**24 live checks against a running server**, all passing — including **ten
traversal attempts**: `../`, `../../etc/passwd`, `Handouts/../GM Only/…`, dot
segments, absolute POSIX and Windows paths, backslashes, and URL-encoded
escapes. Every one 404s with nothing leaked. Search returns two hits for the GM
and **zero** for a player. Anonymous gets 401 everywhere.

**450 tests, ruff clean** (vault 58, notes 14 added).

### A real bug in my own sanitiser

The first `SAFE_URL_RE` was written as "starts with a safe character" and ended
in `[\w./~-]` — which matches the `j` of `javascript:` and the `d` of `data:`.
It permitted **every scheme it existed to block**. Rewritten as an explicit
scheme allow-list that also strips the tab and newline characters browsers
ignore before parsing a scheme, so `java&#9;script:` is caught too. Sixteen
scheme cases are now pinned by test.

Ruff also flagged `"author" in row.keys()` and suggested dropping `.keys()`.
**Applying that would have introduced a bug** — `row` is a `sqlite3.Row`, where
`in` tests *values*, not keys, so the check would have silently become `False`.
Verified empirically before changing it, then removed the conditional entirely
since every caller selects `author`.

---

## Session 7 — 2026-08-13 · Phase 6 complete · **join by QR**

**Where the project stands:** seven phases. A player joins by pointing a phone
camera at the GM's screen — no address typed, no instructions given.

### Shipped

- **`ezvtt/net.py`** — LAN address detection, per-mode join URLs, dependency-light
  SVG QR rendering, best-effort hotspot, and a port-collision probe
- **`ezvtt/routes/network.py`** — `/api/net/join`, GM-only
- **`static/js/join.js`** + panel — QR, address, copy button, alternative
  addresses, and a **full-screen view** to turn the laptop towards the table
- Banner rewritten to show every usable address and point at the on-screen QR
- `--hotspot-ssid` / `--hotspot-password`

### Design notes

**The QR is built from the raw module matrix**, not one of `qrcode`'s image
factories — those pull in Pillow or lxml backends and give back a raster or
markup that cannot be styled. A run of adjacent dark modules becomes one
`<rect>`, which turns ~840 elements into 165 and inlines straight into the page,
so it renders with no network at all.

**Only `internet` mode looks up a public address.** Every other mode must work
with no route to the internet, which `local` and `hotspot` routinely do.

**The full-screen QR is deliberately light-on-dark-page.** A phone camera locks
onto dark-on-white far faster, and the point is for six people to scan at once.

### Verified

- **The SVG reproduces the encoder's module matrix exactly** — the test parses
  the generated SVG back into a matrix and compares. The encoder is a tested
  library; the run-length packing is mine, so that is what is checked.
- Join API is GM-only: player **403**, anonymous **401**
- QR renders at 418×418 px on screen and Escape closes the overlay
- Hotspot on this machine fails honestly: *"This Wi-Fi driver does not support
  the hosted-network interface"* plus the Mobile Hotspot instructions
- `local` and `vps` correctly refuse to invent an address to share

**378 tests, ruff clean.**

### A bug found the hard way

Two servers were listening on 8080 — a stale `local`-mode process on
`127.0.0.1` and the new `lan`-mode one on `0.0.0.0`. **Both bind successfully on
Windows**, neither reports "address already in use", and loopback traffic goes
to the more specific binding. So the stale instance silently shadowed the new
one and I spent twenty minutes convinced a correctly-registered route was
missing.

EzVTT now probes the port before binding and refuses to start with instructions,
rather than starting into a shadowed state with nothing to explain it. That
failure would be far worse for a GM mid-session than for me.

---

## First code review — 2026-08-13 · 8 findings, all fixed

Ran the ruff config that had been in `pyproject.toml` since Phase 0 and never
executed (15 issues), then reviewed the package. **359 tests, lint clean.**

**Confirmed by measurement, then fixed and re-measured:**

1. **`/ws` accepted anonymous connections** and sent them the player snapshot —
   map, scene, campaign name, token positions — on a server whose pages all
   require a login. Starlette runs HTTP middleware only for HTTP scopes, so the
   sign-in gate never covered the socket. Now closed with 1008 at handshake;
   verified refused with 403 while a signed-in player still connects.
2. **Fog compositing blocked the event loop.** `/health` went from 6ms to 740ms
   during a rebuild — every client frozen together. Now `asyncio.to_thread`:
   6ms during a 599ms build. Thumbnails had the same defect and the same fix.
3. **Fog failed open.** When `fog.get()` gave up on an oversized grid, the
   player branch fell through and handed over the raw map URL and an unfiltered
   token list. Now fails closed.
4. **`is_first_run()` ran a SQL COUNT on every request**, static files included,
   because it was the left operand of the `and`. Operands swapped.
5. **Each fog brush tick resent every player a whole snapshot.** Now coalesced
   on a 300ms trailing edge: 12 strokes produced 12 GM updates (unchanged) and
   **1** player update instead of 12.
6. Dead `/ws` branch in the middleware that implied protection it never gave.
7. No-op ternary in thumbnail root resolution.
8. Dead-connection reaping existed in one of five broadcast paths; all five now
   go through a shared `Hub.deliver`.

**Also fixed: a flaky test of my own making.** `test_a_tampered_cookie_is_rejected`
built its "tampered" signature by substituting a fixed `0` for the last
character — one time in sixteen that reproduces the *original* signature and
asserts a valid session is rejected. It failed on this run. Now flips to a
character it demonstrably is not; 0 failures in 40 runs.

`tests/test_regressions.py` covers findings 1, 3 and 4.

Ruff's `S105` and `B008` were false positives here (a settings key name, a SQL
SELECT, and FastAPI's documented `File(...)` idiom) and are suppressed with
stated reasons rather than blanket-ignored. Notably ruff also caught `F821` in
*my own fix* — an undefined `log` that would have raised `NameError` in exactly
the fail-closed path finding 3 added.

### Known gaps

- No rate limiting on `/login`. Worth adding before `internet` mode is used in
  anger.
- Chat history is global rather than per campaign; fine until campaigns are a
  first-class concept.
- Not a git repository. **`git init` has not been run** — awaiting your say-so.
  This is now six phases and 359 tests with no history behind it.

---

## → Next step: Phase 8 — Quality of life

Everything a GM reaches for mid-session that is currently missing. Roughly in
order of how often it would be wanted:

1. **Scenes** — prep several encounters and switch with one click. The `scenes`
   table and `scene_for_map` already exist; what is missing is more than one
   scene per map and a switcher.
2. **Initiative tracker** — the `initiative` table exists and is unused.
3. **Ruler in grid units, and AoE templates** (cone, circle, line).
4. **Ping** — alt-click a spot and everyone sees it. Small, and the thing that
   most reduces "no, the *other* door".
5. **Grid auto-detect** from the map image. The single biggest win for the
   under-a-minute promise, and the fiddliest to get right.
6. Token HP bars and condition markers; undo/redo; handout push; campaign
   export.

**Then Phase 9** (packaging — must run `scripts/gen_third_party_licenses.py`,
since bundled builds do redistribute the dependencies) and **Phase 10** (SRD).

**Still open before 1.0:** no rate limiting on `/login`; chat history is global
rather than per campaign.
