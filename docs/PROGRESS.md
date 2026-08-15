# Progress

The handoff file. Updated at the end of every working session: what shipped,
what was actually **verified** (not merely written), and the precise next step.

Read this and `CLAUDE.md` at the start of a session and you should be oriented
without reading the codebase.

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

## → Next step: Phase 7 — Obsidian vault and player notes

1. `ezvtt/vault.py` — point at a local vault path; read-only Markdown render of
   `.md` with `[[wikilinks]]`, `![[embeds]]`, and frontmatter. **Path-traversal
   guarded and confined to the vault root** — reuse `media.resolve_within`,
   which already has the tests for it.
2. **A folder allow-list.** This is the important one: a raw campaign vault is
   full of GM notes, and one click from a player's screen to your plot outline
   would be worse than no wiki at all. Default to nothing shared.
3. Folder tree and search in a player-facing panel.
4. Player notes: private per-user plus a shared board, persisted in the `notes`
   table that already exists.

**Done when:** a player can read the folders you chose and cannot reach a
single file outside them, confirmed by asking for `../` and for an
un-allow-listed path directly.

**Still open before 1.0:** no rate limiting on `/login`; chat history is global
rather than per campaign; the packaging phase must run
`scripts/gen_third_party_licenses.py`.
