# Progress

The handoff file. Updated at the end of every working session: what shipped,
what was actually **verified** (not merely written), and the precise next step.

Read this and `CLAUDE.md` at the start of a session and you should be oriented
without reading the codebase.

---

## Start here

**State at 2026-08-16.** Phases 0–9 done. **v0.1.0 is built for Windows,
macOS, and Linux** and waiting as a draft release for a human to publish.

| | |
|---|---|
| Branch | `rewrite/fastapi-vtt`, everything committed and pushed |
| PR | [#1](https://github.com/DavidKendig/EzVTT/pull/1) — open, not merged |
| `main` | still the original Java/Django prototype; the PR replaces it |
| Tests | **613**, all passing on Windows, macOS, and Linux |
| Lint | `ruff check .` clean |
| CI | green on 3 platforms, Python 3.10 and 3.12 |
| Release | [v0.1.0](https://github.com/DavidKendig/EzVTT/releases) — **draft**, three archives + SHA256SUMS |

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
shares nothing until you tick a folder · several scenes per map, switched with
one click · an initiative tracker on all three screens · a ruler, and
fireballs the table can see · Alt-click to point at something · **a grid that
lines itself up on upload.**

**Next:** publish the draft release, then Phase 8's remainder (HP bars,
undo/redo, handout push, campaign export) or Phase 10 (SRD). PR #1 is still
open: `main` remains the old prototype. See the bottom of this file.

```bash
.\scripts\build.ps1                        # a single-file EzVTT + checksum
.venv\Scripts\python scripts\smoke_test.py dist\ezvtt.exe
```

**Two things a cold session should not "fix":**

1. `codex.js` sets `innerHTML` from `/api/vault/note` on purpose. That HTML is
   sanitised server-side through a tag allow-list; escaping it again shows every
   note as raw markup.
2. `scripts/stop.ps1` sends a console control event from a child process rather
   than calling `taskkill`. Windows has no SIGTERM for console apps, and doing
   the console dance inline breaks the calling shell.

---

## Session 15 — 2026-08-16 · **v0.1.0 is built**

Pushed six commits, tagged **v0.1.0**, and the release workflow built EzVTT on
three runners in 1m26s:

| | |
|---|---|
| `ezvtt-0.1.0-windows-x64.zip` | 24.7 MB |
| `ezvtt-0.1.0-macos-arm64.tar.gz` | 21.0 MB |
| `ezvtt-0.1.0-linux-x64.tar.gz` | 38.6 MB |
| `SHA256SUMS.txt` | all three |

Every one was smoke-tested on its own platform before packaging. The release is
a **draft** on purpose — the workflow does not publish anything without a human
reading the notes first.

### CI found a real bug on its first run

`test_resolve_within_refuses_escapes["..\..\evil"]` failed on Linux and
macOS. On POSIX a backslash is not a path separator, so that string is an
ordinary filename that lands inside the root — `resolve_within` was right to
allow it, and the *test* was asserting a Windows-shaped answer everywhere. It
now asserts the property that holds on every platform (the result cannot leave
the root) and keeps demanding a refusal where a backslash really does separate.

The suite had never run off Windows before. That is precisely what the matrix
is for, and it earned its keep in the first ninety seconds.

### The tag is one commit behind green

v0.1.0 points at the commit *before* that fix, so its CI run is red for a
test-only reason. The binaries are unaffected — they built and passed their
smoke tests on all three platforms — but if a green tag matters, cut v0.1.1
from `68fd23b` rather than moving a published tag.

---

## Session 14 — 2026-08-16 · Phase 9 · **packaging**

**Where the project stands:** `dist/ezvtt.exe` is 24.5 MB, needs no Python, and
was measured doing the whole job — starting, migrating, rendering, serving its
static files, and printing its licences. What has *not* happened is a real
release: the workflows have never run and no macOS or Linux binary exists yet.

### Shipped

- **`ezvtt.spec`** — one-file build: data directories, the dynamic imports
  uvicorn and Markdown make, and the licence files
- **`scripts/build.ps1` / `build.sh`** — licences, build, smoke test, checksum.
  Refuses to build without the licence file and refuses to ship a build that
  fails the smoke test
- **`scripts/smoke_test.py`** — ten checks against a real running binary
- **`scripts/entrypoint.py`** — the packaged entry point
- **`.github/workflows/ci.yml`** — tests and lint on three platforms and two
  Pythons, a licence job, and a packaged build smoke-tested on each platform
- **`.github/workflows/release.yml`** — tag-triggered matrix build, archives
  with the licence files beside the binary, `SHA256SUMS.txt`, draft release
- **`docs/PACKAGING.md`** and **ADR-016**

### The licence generator was quietly wrong

`gen_third_party_licenses.py` worked from a hand-maintained list of
distributions. Running it for the first time in months: **`sniffio` is not
installed**. anyio had dropped it, and two new transitive packages had appeared
that the file knew nothing about. Nobody neglected anything — upstream changed
*its* dependencies, which no care on this side prevents.

It now computes the closure from `requirements.txt`. Adding a *direct*
dependency is still the deliberate act CLAUDE.md asks it to be; transitive
drift is simply followed. **21 packages, where the list claimed 19 and one of
those did not exist.**

### Four traps, all found by building it

1. **The entry point cannot be `ezvtt/__main__.py`.** PyInstaller runs its entry
   script as `__main__` with no package context, so every relative import in it
   fails — *in the binary only*. Hence `scripts/entrypoint.py`.
2. **Killing a one-file build leaves a process behind.** The bootloader and the
   program it unpacks are two processes; terminating the first leaves a web
   server running and **holding the executable open**, so the next build dies
   with "access is denied" on a file nothing appears to be using. The smoke test
   now kills the tree.
3. **Reading its output can block forever.** Same two processes: the survivor
   inherits the pipe. Output goes to a file instead.
4. **PowerShell's `Set-Content -Encoding utf8` writes a BOM and CRLF**, and both
   break `sha256sum -c` — the BOM corrupts the hash, the carriage return becomes
   part of the filename. Written byte-exact now, and verified with `sha256sum -c`.

### Verified, not just written

- `.\scripts\build.ps1` end to end: **24.5 MB exe, all ten smoke checks, exit 0**
- The smoke test passes against **both** a source checkout and the binary
- A fresh binary against an empty directory: migrations applied, `first_run`
  true, database written **beside the executable**, `/` → `/setup`, the setup
  page rendered, `ezvtt.css` served at 36,855 bytes
- `ezvtt.exe --licences` prints 3,226 lines including the Apache text and the
  dependency licences
- `sha256sum -c` accepts the generated checksum file
- Both workflow files parse as YAML with the jobs and steps intended
- Cold first launch on Windows is ~20s (unpacking plus antivirus); warm is ~4s.
  The smoke test allows 90

**612 tests, ruff clean** — `tests/test_packaging.py` adds 24, including a guard
that fails if a new data directory under `ezvtt/` is ever left out of the spec.

---

## Session 13 — 2026-08-15 · Phase 8 · **grid auto-detect**

**Where the project stands:** drop a battlemap with a grid drawn on it and the
squares already line up. This was the plan's "single biggest win for the
under-a-minute promise, and the fiddliest to get right", and it is done.

### Shipped

- **`ezvtt/gridfind.py`** — edge profiles per axis, autocorrelation for the
  spacing, a phase sweep for the offset, both axes required to agree, and a
  contrast test that separates a drawn line from a texture
- Detection runs **automatically inside the upload**, in a thread, and the map
  arrives with its grid already right
- `POST /api/maps/{id}/detect-grid` and a **Detect grid** button, for maps
  added before this existed or knocked out of alignment since
- The upload toast now says which happened: *"…is on the table, grid and all"*
  or *"…drag the slider to match its squares"*

### The bug that shaped the whole feature

The first version scored the real bundled **Cliff Face** map at 0.41 confidence
and would have applied a 39px grid to it. That map has **no grid at all** — it
is dirt and rock, and its texture *tiles*. Autocorrelation cannot tell those
apart: a tiled texture repeats perfectly and carries no lines whatsoever.

The fix is a second, cheaper test — how much stronger the edges are *on* the
detected lines than across the map as a whole:

| | contrast |
|---|---|
| crisp drawn grid | 15 – 39 |
| faint grid over heavy clutter | 4.8 |
| grid softened by resizing and re-encoding | 2.8 |
| **Cartos gravel texture (no grid)** | **1.01** |

1.01 is exactly what "no lines here" means. Both axes must pass, because a grid
drawn on one axis is floorboards. Written up as **ADR-015**.

### Why it says nothing rather than guessing

The two failures are not symmetric. Missing a grid costs the GM the slider drag
they were going to do anyway. Finding one that is not there means the map
arrives with squares that do not line up and **nothing says so** — they have to
notice before they know to look. Harmonics get the same treatment: a peak at
twice the true spacing is folded down, because a grid twice too large looks
plausible while being silently wrong.

### No new dependency

Pillow's BOX resize collapses an image to one row in C, which is the only
expensive part; the rest is a few hundred thousand multiply-adds over a list.
numpy or OpenCV would have been an acknowledgement entry and a Phase 9
packaging risk for no user-visible gain. See ADR-015 and rule 6 in `CLAUDE.md`.

### Verified, not just written

- **Uploaded a map drawn with a 64px grid at (19, 31)** → it arrived at
  **64.0 px, (18.57, 30.62)**, `detected: true`, with no GM action at all
- **Uploaded a gridless map** → `detected: false`, and it kept the 33.3px
  30-square guess rather than inventing one
- Knocked a detected grid to 210px/(55,5) and pressed **Detect grid**: back to
  64/(19,31), toast *"Grid found: 64 px a square."*
- On the gridless map the same button said *"No grid drawn on this map — set
  the size by hand"* and **changed nothing**
- The endpoint is GM-only: player **403**, anonymous **401**, missing map **404**
- **The event loop stays free**: `/health` answered in **3–6 ms** during a
  229 ms detection of the 1606×7327 map, against 5 ms idle

**588 tests, ruff clean** — `tests/test_gridfind.py` adds 22, over half of them
asserting *silence*: gridless clutter, flat artwork, a perfectly tiling texture,
lines on one axis only, and a thumbnail-sized image.

---

## Session 12 — 2026-08-15 · Phase 8 · **ping**

**Where the project stands:** four of Phase 8's items are done. Alt-click a
square and everyone who can see it gets a marker there for a moment, with the
name of whoever pointed. Grid auto-detect is next, and it is the big one.

### Shipped

- **`ezvtt/ping.py`** — the rate limit, the coordinate check, and the audience
  rule, as pure functions. They *are* the feature, and they are worth being
  able to test without a socket — the same reason `chat.visible_to` lives in a
  module rather than in the hub
- `board.ping` — two rings a beat apart, expanding and fading over 2.4s, drawn
  **over** the fog and labelled with the sender
- The gesture: **Alt-click** points, while **Alt-drag** still moves a token off
  the grid as it always has. Which one it was is only knowable on release, so
  the decision is deferred to `endDrag`

### The three things this program draws on a board

Appended to **ADR-014**, because the distinction is now the interesting part:

| | who sees it | what is kept |
|---|---|---|
| ruler | only the person dragging | nothing |
| **ping** | **everyone allowed that square** | **nothing** |
| template | everyone allowed to see it | a row, until it is cleared |

Any signed-in person may ping — players say "no, the *other* door" at least as
often as the GM — so it is rate-limited to one a second each, and a refused one
is dropped silently, since whoever clicked already knows they clicked.

### The name collision worth remembering

The obvious intent name, `ping`, was **already taken by the socket keepalive**,
which `ws.js` sends every few seconds and the hub answers with a pong. Handing
that name to this feature would have put a marker on everybody's map on every
heartbeat. The intent is `board.ping`; the comment in `_TABLE_INTENTS` says why.

### Verified, not just written

Three live sockets against a running server, on a copy of `data/`:

- GM points at a **revealed** square → GM, player, and projector all get it,
  named *Gary*
- GM points **into the dark** → GM and projector get it; the player gets
  **nothing**
- A **player** points → everyone gets it, named *Player One*
- **Two pings in the same instant → one delivered.** The rate limit holds
- A ping at **NaN** was refused to the sender alone: *"A ping needs a position."*
- The rings draw and expire: 0 marker pixels before, 22 immediately after, 28
  midway, **0** once it lapsed
- The gesture is unambiguous: alt-click fired one ping, alt-**drag** fired none,
  a plain click fired none

**566 tests, ruff clean** — `tests/test_ping.py` adds 17.

---

## Session 11 — 2026-08-15 · Phase 8 · **the ruler and AoE templates**

**Where the project stands:** "can I reach him from here?" is answered by
Shift-dragging, and "who does the fireball catch?" by dropping a circle the
whole table sees. Three of Phase 8's items are done; ping is next.

### Shipped

- **`005_templates.sql`** — `aoe_templates`, geometry in grid units. Named
  `aoe_*` because `ezvtt/templates/` is the Jinja directory and a
  `templates.py` beside it would be a trap for the next person
- **`ezvtt/aoe.py`** — place, update, remove, clear, and the two disclosure
  filters; every field validated rather than trusted
- **`grid.distance_squares` / `distance_feet`** — Chebyshev, pinned by test
- Four GM-only hub intents and `broadcast_templates`
- **`board.js`**: templates drawn under the tokens, dragged out live, hit-tested
  for selection; the ruler on every screen via **Shift-drag**
- A *Measure & templates* panel on the GM screen: circle, cone, line, width,
  colour, hide, delete, clear all

### The rule it rests on

**ADR-014: a template is table state; a measurement is not.** Dropping a
fireball is an announcement and has to look the same on all three screens.
Measuring is a question the asker has, asked several times a turn by everyone,
and most answers are discarded within seconds — sending a message per animation
frame so five other people can watch a line waggle would cost the socket a great
deal for information nobody wants. So anyone signed in may measure, privately;
only the GM places a template. The panel says exactly that, in those words.

`Board#outline` is read by *both* the drawing and the hit-testing, so a template
can never be tested against an outline other than the one on screen.

### Verified, not just written

Against a running server on a copy of `data/`, driving the real canvas:

- Dragging with each tool placed a **circle r=7**, a **cone at 0°**, and a
  **line at 91°**, sizes landing on the half-square steps
- A fireball placed in a **revealed** area reached the player; the three
  dragged over unexplored ground reached **none** of them. Hiding it removed it
  from their list instantly and revealing put it back
- **Ten geometry checks** through the real hit-tester: a point on the cone's
  axis and near its far edge hit; beyond its spread, behind its apex, and past
  its length all missed; the line hit down its length and missed beside and
  past it
- **The ruler draws and clears**: 0 marker pixels before the drag, 253 during,
  211 after release — it stays up to be read — and 0 after Escape. Its label
  read `38.2 sq · 191 ft`, matching an independently computed Chebyshev distance
- A player socket sending `template.place`, `template.remove`, and
  `templates.clear` was refused each time: *"Only the GM can do that."*

**One thing the measuring caught in itself:** the first label read
`38.2 sq · 190.8 ft`, because the feet were derived from the raw distance while
the squares shown were rounded. Both figures now come from the same rounded
number — inviting a GM to check the arithmetic and find it wrong is worse than
showing one decimal fewer.

**549 tests, ruff clean** — `tests/test_aoe.py` adds 33 and the ruler's maths 8.

---

## Session 10 — 2026-08-14 · Phase 8 · **the initiative tracker**

**Where the project stands:** a fight runs on all three screens at once. The GM
rolls the order, hands the turn on, and the projector shows the table whose turn
it is; the creature acting is ringed in green on the board.

### Shipped

- **`004_initiative.sql`** — `initiative.modifier`, `initiative.is_hidden`,
  `scenes.initiative_round`. The table itself has existed, unused, since 001
- **`ezvtt/initiative.py`** — order, turns, rounds, concealment, server-rolled
  d20s, and the repair paths for losing the creature whose turn it is
- Nine hub intents (`initiative.add` · `update` · `remove` · `clear` · `roll` ·
  `start` · `stop` · `advance` · `jump`), GM-only
- **`_initiative.html` + `static/js/initiative.js`** — one panel, three
  surfaces: interactive for the GM, read-only for the player view and the
  projector, and on those two it appears only while a combat is running
- `board.setCurrentToken` rings the acting creature in green — distinct from
  the brass dashed selection, so a GM can have one token selected and see whose
  turn it is at the same time

### The rules it rests on

Written up as **ADR-013**:

1. **Per scene, not per table.** Switching away mid-fight and back returns to
   round four with the right creature up. Since scenes shipped this session,
   that is a thing GMs will actually do.
2. **A concealed entry never reaches a player.** It is inherited from the token
   and **re-synced when the token is hidden or revealed** — otherwise revealing
   the ambush is a two-step act, and hiding a token again leaves its name in the
   players' order.
3. **The d20 is thrown on the server.** There is nowhere in the intent to put a
   result.

**One leak is admitted rather than papered over.** When a concealed creature is
acting, players get the round but no current entry: they can tell *someone* they
cannot see is in the fight. The turn has to pass through that slot. The
identity, the score, and the head-count stay hidden, and freezing their round
number instead would make the tracker lie to them.

### Verified, not just written

Again against a running server on a **copy** of `data/`:

- **Add tokens** with only scenery on the board answered *"Put some creatures on
  the board first"* — it adds the creature layer, not the furniture
- Three creatures added under their own labels; the hidden one **joined
  concealed**, dimmed with a *Reveal* action rather than a *Hide* one
- **Roll all** produced 14 / 12 / 6 and the list re-sorted; **Start combat** put
  the top of the order up on round 1
- **Three live sockets through a whole round.** GM and projector saw all three
  entries; the player saw two. When the concealed assassin came up on round 2
  the player got `round: 2, current_id: null` — the round moved, the name did
  not arrive. Revealing the assassin's *token* put it into the players' order in
  the same instant. Deleting it mid-turn handed the turn to Brand **without**
  ticking the round over
- The projector drops concealed entries client-side (it authenticates as the GM,
  so it receives them) and carries no controls; its panel disappears when the
  combat ends
- Clicking a row jumped the turn to it and left the round alone
- **The green turn ring draws:** 0 matching pixels before `setCurrentToken`,
  116 after. Measured on a `Board` built for the purpose, because this session's
  browser pane is not displayed and the render loop skips while `document.hidden`

**508 tests, ruff clean** — `tests/test_initiative.py` adds 36, including the
two repair paths and both concealment rules.

---

## Session 9 — 2026-08-14 · Phase 8 begins · **scenes**

**Where the project stands:** a GM can prep several encounters over the same
battlemap and switch the table between them with one click. Phase 8's first and
most-wanted item is done; the initiative tracker is next.

### Shipped

- **`003_scenes.sql`** — `scenes.last_active_seq`
- **`state.py`** — `list_scenes` · `get_scene` · `rename_scene` ·
  `duplicate_scene` · `delete_scene` · `scene_ids_for_map`; `scene_for_map` now
  resolves to the scene *last run* over that map
- **`ezvtt/routes/scenes.py`** — `/api/scenes` create · rename · duplicate ·
  delete, GM-only at the router (ADR-010)
- Hub `scene.activate` takes a `scene_id` as well as a `map_id`
- **Scenes panel** on the GM screen: every scene with its map, its token count,
  and Rename / Copy / Delete. The board bar names the *scene*, adding the map
  name beside it only when the two differ
- Deleting a map now clears its scenes' fog composites off disk, which the old
  one-scene-per-map path had been leaking

### The three decisions worth remembering

Written up as **ADR-012**; the short version:

1. **Clicking a map returns to the scene last run**, not the map's oldest scene.
2. **A new or copied scene is not activated.** Adding one is prep; putting it in
   front of the table is the click on the scene itself.
3. **A scene name never reaches a player.** Their payload carries the scene id
   and nothing else about it — names are where prep gets written down.

### A bug the test found and reasoning would not have

`last_active_at` was first a `datetime('now')` timestamp. Two activations a
moment apart recorded **the same value** — second resolution, and Windows'
clock granularity defeats the sub-second formats too — so the tie broke by id
and sent the GM back to the wrong scene. That is the exact failure the column
existed to prevent, and it passed review by inspection. It is now
`last_active_seq`, `MAX(seq) + 1` inside the activating transaction.

### Verified, not just written

**Against a running server**, on a *copy* of `data/` so the real campaign was
never touched (`EZVTT_DATA_ROOT` pointing at a scratch tree):

- Migration 003 applied cleanly to the existing 855-thumbnail database, with the
  one pre-existing scene preserved
- **Copy** produced a second scene with all 16 tokens and a byte-identical fog
  mask (109 of 1258 cells revealed on both), and did **not** take the table
- After switching to the copy, clearing its tokens and revealing all its fog,
  the original still held 16 tokens and 109 revealed cells — **the copy is
  genuinely independent**
- Clicking the map in the library came back to the scene last run, not the first
- A new scene opens empty and **0% revealed**; renaming updates the board bar
  live; deleting the active scene empties the board and leaves the switcher up
- **Three live sockets during a switch:** display and GM received the scene with
  its name and the original map URL; the player received `{'id': 2, 'map_id': 1}`
  with **no name**, no `scenes` key, and the composite URL for the new scene
- `/api/scenes` refuses anonymous **401** and a signed-in player **403** on all
  five endpoints

**472 tests, ruff clean** — `tests/test_scenes.py` adds 22, including the
independence of a copy and both spoiler rules.

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

1. ~~**Scenes**~~ — done, Session 9.
2. ~~**Initiative tracker**~~ — done, Session 10.
3. ~~**Ruler and AoE templates**~~ — done, Session 11.
4. ~~**Ping**~~ — done, Session 12.
5. ~~**Grid auto-detect**~~ — done, Session 13.
6. Token HP bars and condition markers; undo/redo; handout push; campaign
   export.

**Then Phase 9** (packaging — must run `scripts/gen_third_party_licenses.py`,
since bundled builds do redistribute the dependencies) and **Phase 10** (SRD).

**Still open before 1.0:** no rate limiting on `/login`; chat history is global
rather than per campaign.
