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

## → Next step: Phase 6 — Run modes and networking

The plumbing exists (`--mode` already drives binding and the security policy);
this phase makes the networked modes genuinely usable.

1. `ezvtt/net.py` — LAN address, public IP lookup, and **QR code generation**
   for the join URL. `qrcode` is already a dependency.
2. Show the join URL and QR on the GM screen, not only in the console — the GM
   is looking at the browser, not the terminal.
3. `hotspot` mode: best-effort AP via Windows Mobile Hotspot / `nmcli`, with the
   warnings already written in `docs/RUN_MODES.md` surfaced **in the app**, and
   an automatic fall back to `lan` when it fails.
4. `internet` mode: print the port to forward, and warn plainly about plain HTTP.
5. `vps` mode: verify the `X-Forwarded-*` trust path behind a real proxy.
6. Optional `ezvtt.local` via mDNS so players need not type an IP.

**Done when:** a phone joins the table by pointing its camera at the GM screen.
