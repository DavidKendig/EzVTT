# Architecture Decision Record

Decisions that are **settled**. Each entry records what was chosen and, more
importantly, *why* — so that a later session (human or AI) does not relitigate a
question that was already worked through, and so that anyone proposing a change
knows what they are arguing against.

Add new entries at the bottom. Do not delete entries; supersede them with a new
one that references the old.

---

## ADR-001 — Single Python process, not a Java gateway fronting Django

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** EzVTT is one FastAPI application served by one uvicorn process on
one port. Authentication, WebSockets, static files, media, and persistence all
live in that process.

**Context.** An earlier EzVTT prototype used a Java edge gateway that terminated
connections, owned auth, and reverse-proxied to a localhost-only Django app.
That design had a real security argument behind it — only one hardened process
faced the network.

**Why we changed.** In practice most of the complexity budget went to plumbing
between the two runtimes: an identity header passed over localhost, two
lifecycles to start and stop, two dependency stories, and a much harder path to
a single-file executable. The isolation benefit was largely theoretical for an
application a GM runs on a laptop for four friends.

**Consequences.** `stop` and `kill` manage exactly one PID. Packaging is one
PyInstaller invocation. The trade accepted: no process-level isolation between
the network edge and application logic, so route-level authorisation has to be
correct on its own. That is why ADR-004 exists.

---

## ADR-002 — SQLite with plain `.sql` migrations, no ORM

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** Persistence is stdlib `sqlite3` against `data/ezvtt.db`. Schema
changes are numbered `.sql` files in `ezvtt/migrations/` applied in order by a
small runner in `ezvtt/db.py`.

**Why.** The data model is small and well understood. An ORM plus a migration
framework would add two heavyweight dependencies, meaningful PyInstaller
friction, and an abstraction layer over roughly a dozen tables. SQLite needs no
server, which matters because the target user is a GM who wants to run one
command.

**Consequences.** Queries are written by hand and must be parameterised — never
string-formatted — to avoid injection. Concurrency is single-writer; acceptable
for table-sized player counts, and revisit only if it actually bites.

---

## ADR-003 — Assets fetched by script, never committed

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** The Tom Cartos bundle (800 files, ~537 MB) stays out of git.
`scripts/fetch-assets` installs it into `assets/bundled/`, which is gitignored
apart from its licence notice.

**Why.** A half-gigabyte clone for every contributor is a poor trade for
convenience that a single setup step provides. It also keeps a clean separation
between Apache-licensed code that we own and third-party artwork that we do not.

---

## ADR-004 — The server is authoritative; clients send intents

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** Clients never mutate shared state. They send *intents*
(`move_token`, `reveal_cell`, `roll`) over the WebSocket. The server checks the
sender's role, writes to SQLite, then broadcasts the resulting state.

**Why.** Two features are worthless if the client is trusted:

- **Fog of war.** If hidden map regions are sent to the browser and merely
  covered with an overlay, any player can open developer tools and see the whole
  map. Hidden data must never leave the server.
- **Dice.** If the client reports the result, a modified client can report a 20
  every time.

**Consequences.** Every WebSocket message handler performs its own authorisation
check. This is the compensating control for the loss of process isolation in
ADR-001, and it is not optional.

---

## ADR-005 — Bundled Cartos artwork is never modified on disk

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** EzVTT does not write to, crop, re-encode, or otherwise alter any
image in `assets/bundled/`. Grid-footprint sizing is stored as `grid_w`/`grid_h`
metadata and applied as a display-time transform over the untouched original.
Art the user uploads themselves has no such restriction.

**Why.** Tom's Open Map License does not permit editing these assets. This is a
licence obligation, not a preference.

**Consequences.** Any code path that could write into `assets/bundled/` is a bug.
Thumbnail generation writes to `data/thumbs/`, keyed by source path — never
in place. The relevant functions carry a comment pointing here.

---

## ADR-006 — No frontend build step, no CDN, dark theme by default

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** The frontend is hand-written ES modules and Canvas 2D, served as
written. Styling is hand-written CSS using custom properties. No bundler, no
framework, no CDN. **The interface defaults to dark mode.**

**Why.**

- *No build step* — a contributor edits a `.js` file and reloads. No `npm
  install`, no node_modules, no toolchain drift. The board renderer is Canvas
  drawing code, which gains nothing from a component framework.
- *No CDN* — EzVTT must work with no internet connection at all. That is not a
  nice-to-have: `local` and `hotspot` modes frequently run with no route to the
  internet by definition.
- *Dark by default* — this software is used in dim rooms, and one of its two
  windows is projected on a shared screen for a whole table. A white background
  in that setting is genuinely unpleasant and washes out map art. A light theme
  may be offered later as an option; dark is the default and the design target.

**Consequences.** CSS custom properties are declared once at `:root` so a light
theme is a variable override rather than a rewrite. Contrast is checked against
map art, not against a blank page.

---

## ADR-007 — Passwords hashed with stdlib `scrypt`; sessions signed with stdlib `hmac`

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** `hashlib.scrypt` with a per-user random salt for passwords.
Session cookies signed with `hmac` and compared using
`hmac.compare_digest`. No `bcrypt`, `argon2-cffi`, or `itsdangerous`.

**Why.** `scrypt` is a memory-hard KDF in the standard library and is entirely
adequate here. `bcrypt` and `argon2-cffi` are compiled extensions that
complicate cross-platform PyInstaller builds — a real cost against no meaningful
security gain at this threat level.

**Consequences.** Cost parameters live in one constant in `ezvtt/auth.py` and
are recorded per-hash so they can be raised later without invalidating existing
passwords.

---

## ADR-008 — Link to dependency licences; bundle them only in packaged builds

**Date:** 2026-08-12 · **Status:** Accepted

**Decision.** `README.md` credits each dependency and links to its licence at
source. It does not reproduce licence text. A generated
`THIRD_PARTY_LICENSES.md` ships only with packaged single-file builds, produced
by `scripts/gen_third_party_licenses.py`.

**Why.** Installing EzVTT from source does not redistribute its dependencies —
`setup` fetches them from PyPI on the user's own machine, under their own
licences, direct from their authors. The "include a copy of the licence"
obligations in the Apache, BSD, and MIT terms attach to redistribution, which
begins when a PyInstaller executable embeds those packages.

Separately: licence text transcribed by hand drifts from the real thing and
misattributes clauses, producing an authoritative-looking file that is wrong.
The generator reads each licence byte-exact from the installed distribution's
own metadata and **fails the build** if a licence cannot be located, so a
compliance gap is loud rather than silent.

**Consequences.** Phase 9 packaging must run the generator and include its
output in the bundle. `--check` mode exists for CI.

---

## ADR-009 — The beta bypass signs in as a real account

**Date:** 2026-08-13 · **Status:** Accepted

**Decision.** The bypass button creates (once) an ordinary `beta-gm` account
with a random, unusable password and issues a normal session. There is no
synthetic principal. The account has role **gm**, not admin, and does **not**
satisfy first-run.

**Context.** Phase 0 shipped a `BYPASS_GM` constant that the request path
substituted for a real principal. That meant two kinds of identity flowing
through every guard, and any code that assumed `user_id is not None` had a
second case to get wrong.

**Why.** One authentication path is one thing to secure. Route guards, the
WebSocket handshake, the admin screen, and session listing all treat a bypassed
GM as an ordinary user because it *is* one. Granting GM rather than admin means
the escape hatch cannot be used to manage accounts. Not satisfying first-run
means the wizard keeps asking until a real administrator exists.

**Consequences.** The bypass account appears in the admin account list, clearly
labelled, and can be deleted there. Starting once in `internet` or `vps` mode
writes `beta_bypass = 0` and that persists into later local runs until an admin
re-enables it — failing safe in the direction that matters.

---

## ADR-010 — Guards are applied at the router, not per handler

**Date:** 2026-08-13 · **Status:** Accepted

**Decision.** Authorisation lives in `ezvtt/deps.py` and is attached to each
router with `dependencies=[Depends(require_gm)]`. Handlers do not repeat it.

**Context.** Phases 1 and 2 each grew a private `_require_gm` helper copied
between route modules, checked by hand at the top of every handler.

**Why.** A copied check is one a new endpoint can be written without. Attaching
it to the router inverts the default: a new route under `/api/maps` is guarded
because of where it lives, and making one public has to be deliberate.

**Consequences.** Routers must be grouped by required role. A route needing a
different role belongs on a different router, not an inline exception.

**Amended 2026-08-13 (first code review).** Router-level guards cover HTTP only.
Starlette does not run HTTP middleware for WebSocket scopes, so `/ws` was
accepting unauthenticated connections and handing them the player snapshot —
map, scene, campaign name, and every visible token position — on a server whose
pages all required a login. `board_socket` now checks
`principal.is_authenticated` and closes with 1008 before accepting.

A misleading `if path not in ("/ws",)` line in the HTTP middleware was what made
this look handled; it could never run. It has been removed and replaced with a
comment saying where the socket's guard actually lives. **Anything reached over
a WebSocket needs its own check** — the middleware will not do it for you.

---

## ADR-011 — Players are served a composited map, never the original

**Date:** 2026-08-13 · **Status:** Accepted

**Decision.** `/media/maps/` is GM-only. A player's snapshot points at
`/media/fog/{scene}?v={version}`: a copy of the battlemap with unrevealed cells
painted black, generated on the server, cached per fog version, and downscaled
to 4096px on its long edge. The fog mask itself is never sent to a player, and
tokens standing in unrevealed cells are omitted from their payload.

**Context.** The obvious way to draw fog is a black overlay on the canvas. It
looks identical and is far less work.

**Why we did not.** It is theatre. The browser has already downloaded the whole
battlemap to draw it, so any player can open the network tab and look at the
untouched image — including the ambush, the secret door, and the rest of the
dungeon. ADR-004 says hidden data must not leave the server, and an overlay
breaks that promise while appearing to keep it. The README makes this claim to
users; it has to be true.

**Alternatives considered.** Tiling the map and gating tiles by revealed cell is
more precise and avoids recompositing, but a tile covers several cells, so
partial tiles either leak their whole area or need per-tile masking and an
unbounded cache. Compositing is simpler, exact at cell granularity, and cheap
enough at composite resolution.

**Consequences.** A reveal costs one image rebuild — measured at roughly 180ms
for a 12-megapixel map, done off the event loop and cached until the fog changes
again. Superseded composites are pruned. The GM screen still loads the original
and draws fog as a translucent overlay, because they are allowed to see through
it; the display window loads the original too and paints fog fully opaque, since
it is GM-authenticated but projected to the table.

The composite is lossy WebP, which rings by about one unit of luminance within
~16 pixels of a fog boundary and is exactly black beyond. Lossless would remove
that at six times the file size and three times the build time; the ringing sits
alongside pixels the player can already see and carries no usable information.
`tests/test_fog.py` bounds it rather than assuming it.

**Amended 2026-08-13 (first code review).** Two things this ADR asserted were
not actually true when written, and are now:

- *"Done off the event loop."* It was not. `serve_fog_composite` called Pillow
  inline, and a `/health` probe during a rebuild went from 6ms to 740ms — the
  whole server froze for the duration, for every client at once. Now dispatched
  with `asyncio.to_thread`; measured at 6ms during a 599ms build. Thumbnail
  generation had the same defect and the same fix.
- *Fog failed open.* `state.snapshot` only substituted the composite when fog
  state existed; when `fog.get()` gave up (a map needing more than `MAX_CELLS`
  cells) the player branch fell through and handed over the original map URL and
  an unfiltered token list. It now fails **closed** — no map, no tokens — and
  logs why.

---

## ADR-012 — A scene is the unit of prep; several may share one map

**Date:** 2026-08-14 · **Status:** Accepted

**Decision.** A scene is a map *plus* its own tokens and its own fog. A map may
carry any number of them. Clicking a map in the library puts back the scene that
was last on the table, not the map's oldest scene. Creating or copying a scene
does **not** activate it. A scene's name is GM-only and never reaches a player's
payload.

**Context.** Phase 1 kept scenes one-to-one with maps and created them on demand,
so a GM never had to learn what a scene was to get a map on the table. That is
still true — uploading a map makes its first scene silently. What was missing is
the second scene over the same artwork: "the tavern" and "the tavern, after the
fight" are the same picture with different monsters standing on it and different
parts of it revealed.

**Why last-run rather than first.** With one scene per map the two were the same
thing. With several, ordering by id would take a GM who clicked *Cliff Face*
back to the encounter they finished two sessions ago, silently and with no way
to tell from the library that it had happened.

**Why a counter, not a timestamp.** The obvious implementation stamps
`last_active_at` with `datetime('now')`. That has second resolution, and even
sub-second SQLite formats inherit the platform clock's granularity — on Windows
two switches a moment apart recorded the *same* instant, and the tie then broke
by id, which is exactly the bug the column existed to fix. This was caught by a
test, not by reasoning. `last_active_seq` is `MAX(seq) + 1` inside the same
transaction that activates: it cannot tie, and it does not care what the clock
does over DST or an NTP correction.

**Why a new scene is not activated.** Adding or copying a scene is prep. Putting
it in front of the table is the click on the scene itself. A GM tidying their
scene list between fights must not blank the projector to do it.

**Why the name is GM-only.** Scene names are where prep gets written down —
"Ambush at the bridge", "The traitor reveals himself". The player payload carries
the scene's id so a client can tell one from another across a switch, and nothing
else about it. Same rule as fog and hidden tokens: it is not filtered on the
client, it is never built into their snapshot. See ADR-004.

**Consequences.** Deleting a scene takes its tokens and fog with it and leaves
the map alone. Deleting the *active* scene leaves nothing on the table rather
than promoting a sibling — switching the room to a different encounter unasked
is worse than an empty board, and the switcher is right there. Copying a scene
copies its fog mask as well as its layout; a duplicate of a half-explored
dungeon that arrived fully concealed would not be a copy of it.

---

## ADR-013 — The initiative tracker belongs to the scene, and conceals entries

**Date:** 2026-08-14 · **Status:** Accepted

**Decision.** Turn order and round number are per scene, not per table. Entries
may be **concealed**, and a concealed entry is absent from a player's payload
rather than flagged in it. An entry bound to a token inherits that token's
concealment and keeps following it. The GM's screen, the player view, and the
projector all render the same component; only the GM's is interactive.

**Why per scene.** Combat belongs to the encounter. Since ADR-012 a GM can
switch to another scene mid-fight — to show a map of the wider region, or
because the party fled somewhere prepped — and switching back must return to
round four with the right creature acting. A table-wide tracker would have
silently reset the fight. `scenes.initiative_round` at zero means no combat is
running there, which is also what makes the panel appear and disappear on the
player and projector views without a separate flag.

**Why entries can be concealed.** A GM rolls the ambush into the order before
the party knows there is one. Concealment is inherited from the token when the
entry is created and **re-synced whenever the token is hidden or revealed** —
without that, revealing the ambush would be a two-step act (token, then entry)
and, worse, hiding a token again would leave its name sitting in the players'
turn order.

**The residual leak, stated plainly.** When the creature acting is concealed, a
player is sent the round but no `current_id`: they see the round tick over with
nobody highlighted. That tells them *someone* they cannot see is in the fight.
There is no way around it short of not showing players the tracker at all — the
turn has to pass through that slot. What stays hidden is the identity, the
score, and the count, which is the part that matters. The alternative,
freezing the round number on their screen, would make the tracker lie.

**Why rolling is server-side.** `initiative.roll` carries a request, never a
result, and `dice.evaluate` throws the d20 here. Same rule as chat rolls: there
is nowhere in the intent for a client to put a number. See ADR-004.

**Consequences.** The tracker has to survive the creature whose turn it is
dying, which is the most ordinary thing that can happen to it: the initiative
row cascades away with its token, so `initiative.get` repairs a combat left with
no current entry as it reads, and `remove` works out the successor *before* the
delete, from an order the doomed row is still part of. Removing a creature never
ticks the round over — a creature dying is not the table taking a turn.

Ending a combat keeps the order and only zeroes the round; the party is still
the party after the fight, and retyping four names is not a feature. Clicking a
row hands the turn straight to it, because "no, we skipped Anya" is the most
common correction at a table and should not cost four clicks and a round.

---

## ADR-014 — A template is table state; a measurement is not

**Date:** 2026-08-15 · **Status:** Accepted

**Decision.** Area-of-effect templates -- circle, cone, line -- are stored per
scene and broadcast to everyone. The ruler is drawn entirely on the screen of
whoever is dragging it and never touches the server. Anyone signed in may
measure; only the GM may place a template.

**Why the split.** Dropping a fireball is an announcement: the point is that
the table sees whose square it covers, and the answer has to be the same on all
three screens. Measuring is a question the person asking has -- "can I reach
him from here?" -- and it is asked several times a turn, by everyone, most of
the answers being discarded immediately. Sending a message per animation frame
so that five other people can watch a line waggle would cost the socket a great
deal for information nobody wants. So: measure freely and privately; drop a
template when the table needs to see it. The GM screen says exactly that, in
those words, under the tools.

**Why players may measure but not place.** A ruler changes nothing, so it does
not need the rights that changing something does -- and a player working out
their own move without asking is the whole reason to give them a board at all.
A template is shared, persistent state; letting five people write to it invites
a tidying problem the GM does not need mid-combat. Player-placed templates are
deferred, not refused.

**Geometry.** Distance is Chebyshev -- a diagonal costs the same as a straight
step -- which is the 5e rule and the one the table will be counting with unless
they have deliberately chosen otherwise. Euclidean would read as more precise
while disagreeing with how the group actually moves. A cone is as wide at its
far end as it is long, putting its edges at `atan(0.5)` either side of where it
points; every other cone dimension follows from that one number. Squares are
shown before feet, because squares stay true on a map drawn to some other scale.

**Consequences.** `Board#outline` is read by both the drawing and the
hit-testing, so a template can never be tested against an outline other than the
one on screen. Geometry is stored in grid units, like token positions, so
adjusting the grid does not scatter what is already placed. Templates obey the
same disclosure rules as tokens: a concealed one -- a glyph of warding drawn
where it lies -- is absent from a player's payload, and so is one drawn entirely
over map they have not revealed, since a circle over unexplored ground is a map
of the unexplored part. The colour is validated as hex before it is stored,
because it reaches a canvas fill style on every client.

**Amended 2026-08-15 — ping is the third case.** Alt-clicking marks a spot on
every screen that can see it, and stores nothing:

| | who sees it | what is kept |
|---|---|---|
| ruler | only the person dragging | nothing |
| **ping** | **everyone allowed that square** | **nothing** |
| template | everyone allowed to see it | a row, until it is cleared |

Persisting a ping would mean choosing when it expires and reconciling that
across five clients, for a mark that has done its job before anyone could ask
what happened to it. It is a finger on a shared screen, not a thing on the map.

**Any signed-in person may ping**, players included -- "no, the *other* door" is
said by them at least as often, and the marker changes nothing. What that opens
is noise, so it is rate-limited to one a second per person; a refused ping is
dropped silently, since the person who clicked already knows they clicked.

The fog rule applies unchanged: a player is not sent a ping standing on ground
they have not revealed. The GM and the projector see all of them -- the
projector is showing the real map with the fog drawn over it, so a marker in a
concealed corridor is information the GM already has. And `clean_point` refuses
NaN explicitly, because it survives JSON, compares false against every bound,
and would be drawn at no position at all on every client that received it.

---

## ADR-015 — Grid detection is autocorrelation over edge profiles, and it says when it does not know

**Date:** 2026-08-15 · **Status:** Accepted

**Decision.** `ezvtt/gridfind.py` reads a map's grid from the artwork using
Pillow and about two hundred lines of arithmetic: sum the edge strength down
each column and across each row, autocorrelate each profile to find the
spacing, sweep the phase to find where the first line sits, and require the two
axes to agree. It runs automatically on every upload and behind a button
otherwise. **When it is not confident, it returns None** and the map keeps the
existing 30-square guess.

**Why this and not OpenCV, or a Hough transform, or numpy.** A drawn grid is
the most strongly periodic thing on a battlemap, and autocorrelation over a
one-dimensional profile finds a period in a few hundred thousand multiply-adds
— 15ms for a typical map, 230ms for a 12-megapixel one, off the event loop. A
Hough transform would find the lines individually, which is the harder problem
and the one worth solving only for *rotated* grids; those are rare enough to
leave. OpenCV is a 60MB wheel and a packaging risk for Phase 9 (ADR-008), and
numpy is an acknowledgement entry and a second binary dependency, for a
speed-up on the one part that Pillow's BOX resize already does in C.

**The bug that shaped this.** The first version scored the real bundled
*Cliff Face* map at 0.41 confidence and would have applied a 39px grid to it.
That map has no grid at all — it is dirt and rock, and its **texture tiles**.
Autocorrelation cannot tell those apart: a tiled texture repeats perfectly and
carries no lines whatsoever. The fix is a second test, `_line_contrast`, asking
how much stronger the edges are *on* the detected lines than across the map as
a whole. Drawn grids measure 3 to 39. The gravel measures **1.01** — the same
as everywhere else, which is exactly what "no lines" means. Both axes must
pass, because a grid drawn on one axis is floorboards.

**Why silence beats a guess.** The two failures are not symmetric. Missing a
grid costs the GM the slider drag they were going to do anyway. Finding one
that is not there means the map arrives with squares that do not line up and
nothing says so — the GM has to notice before they know to look, which is worse
than the problem this feature exists to remove. Harmonics get the same
treatment: a peak at twice the true spacing is folded down, because a grid
twice too large looks entirely plausible while being silently wrong.

**Consequences.** Detection runs inside the upload request, in a thread —
measured at 3-6ms for `/health` during a 229ms detection, against 5ms idle.
Rotated and hex grids are out of scope and simply return None. The confidence
number is reported to the caller but not shown to the GM: "it found a grid" and
"it did not" are the only two things they can act on.

---

## ADR-016 — One file per platform, state beside it, licences inside it

**Date:** 2026-08-15 · **Status:** Accepted

**Decision.** A release is a single PyInstaller one-file executable per
platform, shipped in an archive with `LICENSE`, `NOTICE`,
`THIRD_PARTY_LICENSES.md`, and `README.md`. The campaign database and uploads
live in `data/` **beside the executable**, never inside it. Map artwork is not
bundled. Nothing is published that has not started, served a page, and printed
its licences on the platform it was built for.

**Why one file.** The audience is a GM who wants to run a game, not install
software. Download one thing, double-click it, and the browser opens. A
one-directory build is faster to start but arrives as a folder of two hundred
files where the executable is one of them, and "which of these do I click" is
exactly the friction this project exists to remove. The cost is a slow first
launch — a 25 MB binary unpacks itself and is scanned by antivirus while it
does, measured at 20 seconds cold on Windows and a few seconds warm.

**Why state goes beside the executable.** A one-file build unpacks into a
temporary directory that is deleted on exit. Writing the campaign there would
lose it, silently, the first time. Putting it beside the executable also makes
"back up my campaign" mean "copy that folder", and lets a GM carry the whole
thing on a stick. `EZVTT_DATA_ROOT` overrides it for anyone who wants the
program in one place and the campaign in another.

**Why the artwork stays out.** 537 MB of Tom Cartos assets under a separate
licence would dominate the download, and ADR-003 already keeps it out of the
repository for the same reason. `scripts/fetch-assets` installs it beside the
executable, where the program looks for it.

**Why the licences ship twice.** Inside the binary, so `ezvtt --licences` works
for someone who has only the executable, and beside it in the archive, so
unpacking shows them without running anything. ADR-008 committed this project
to shipping dependency licence texts in bundled builds; a file only reachable
by running the program is a thin reading of that obligation.

**Consequences, all of which were found by building it.** The entry point has
to be a separate `scripts/entrypoint.py`, because PyInstaller runs its entry
script as `__main__` with no package context and every relative import in
`ezvtt/__main__.py` fails — in the binary only. Templates, static files, and
migrations are listed in `ezvtt.spec` by hand, since nothing imports them.
uvicorn and Markdown choose modules by name at runtime, so both are collected
wholesale. UPX is off: packed binaries get quarantined by antivirus, which is a
worse first impression than a larger download.

**The licence generator had to be rewritten.** It worked from a hand-maintained
list of distributions, which had already drifted: `anyio` dropped its
dependency on `sniffio`, and two new transitive packages had appeared. The list
was not wrong through neglect — it was wrong because upstream changed *its*
dependencies, which no amount of care on this side prevents. The closure is now
computed from `requirements.txt`, so adding a direct dependency is still the
deliberate act CLAUDE.md asks it to be, while transitive drift is simply
followed. It found 21 packages where the list claimed 19 and one of those did
not exist.

---

## ADR-017 — Players see a health bar in quarters, never the numbers

**Date:** 2026-08-16 · **Status:** Accepted

**Decision.** A token may carry hit points and conditions. Conditions are
public. Hit *points* are not: a player is sent a bar in quarters, and only when
the GM has left it public. The exact numbers go to the GM, and to the player who
owns that token.

**Why quarters rather than a fraction.** The obvious implementation sends
`hp / hp_max` and lets the client draw a bar. But a player who knows a monster
has 11 hit points and receives `0.6363` has just been told it is on 7 — the
numbers were withheld and then handed over in a different notation. Four steps
carry what a glance at a bar actually tells you: full, most, half, nearly gone.
Rounded *up*, so a creature clinging on at one hit point shows a sliver rather
than an empty bar; an empty bar reads as dead, and the difference matters to
whoever is deciding whether to run.

**Why conditions are public.** A prone goblin is prone in front of everyone at
the table. Concealing that would be modelling a fiction nobody plays.

**Why the owner gets numbers.** It is their character. Hiding a player's own hit
points from them would be absurd, and every table tracks them on a sheet anyway.

**What it cost.** Health is the first field whose *value* differs between two
players, which broke an assumption three broadcast paths were built on:
`broadcast_state` built one player snapshot and fanned it out, and
`broadcast_token` sent one payload to everybody. Both now build per viewer —
snapshots memoised per `user_id` so a player with two tabs open costs one build,
and token deltas shaped into at most three payloads (the GM's, the owner's,
everyone else's) regardless of how many people are at the table.

**Consequences.** The condition vocabulary is a fixed sixteen entries with
two-letter badge codes, sent to clients with the table so the names live in one
place; "whatever the client sent" is not something to draw on a canvas. `dead`
and `deafened` get DD and DF, because both would otherwise be DE and that is a
poor thing to be vague about. A token with no hit points set says nothing about
health at all, which is how the several hundred barrels on a battlemap stay
quiet.

---

## ADR-018 — A handout is one image, held up to the whole table

**Date:** 2026-08-16 · **Status:** Accepted

**Decision.** Handouts are a small library of images with exactly one "showing"
at a time, stored as a setting rather than a scene property. Showing one puts it
full-screen on every surface, the GM's included. The GM's close takes it down
for everyone; anyone else's closes their own copy, and the next push brings it
back.

**Why a library rather than a push-and-forget.** The same handout comes back out
three sessions later. Uploading it again, and hunting the file on disk to do
that, is exactly the friction this program exists to remove.

**Why one at a time.** Holding something up means holding *one* thing up. Two
overlays racing each other on a projector is not a feature, and "which of these
is the table looking at" has to have an answer.

**Why it is not a scene property.** What the table is looking at right now
outlasts putting a different map down — a GM showing a letter while switching to
the map of the district should not have the letter vanish underneath them. It
comes down by a deliberate act.

**Why the GM sees it too.** It is how they know what is on the projector without
turning round. This is the one thing in EzVTT where every audience is shown
exactly the same payload, and that is the point of it.

**Why a viewer may close their own copy.** A player who has read the letter
wants their board back, and asking the GM for permission to stop looking at a
picture is absurd. The dismissal is per handout, so the next thing pushed
appears: "I dismissed the previous one" is not an answer to a new one.

**Consequences.** `data/handouts/` is a media root players may read, unlike
`data/maps/`, which is GM-only because fog depends on it (ADR-011). Showing one
is therefore a deliberate act of publication, and the only images in it are ones
a GM uploaded to show. Deleting the handout that is up clears the pointer, and
`showing()` resolves through `get()` so a pointer left dangling by any other
route answers "nothing is showing" rather than a broken image on five screens.
