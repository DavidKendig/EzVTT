# EzVTT

**A virtual tabletop built around one goal: the GM gets a map on the table and starts playing in under a minute.**

Most VTTs are powerful and slow to set up. EzVTT trades breadth for speed — drop in a map, snap the grid, scatter some props, and run the session. It works on a laptop with a second monitor at your kitchen table, over your home Wi-Fi so players can use their phones, or on a cloud VPS for a fully remote game.

> **Status: pre-release / in active development.** See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for the roadmap and [`docs/PROGRESS.md`](docs/PROGRESS.md) for what actually works today. A login-bypass button is present during beta — see [Beta bypass](#beta-bypass).

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Run modes](#run-modes)
- [Scripts](#scripts)
- [Map assets](#map-assets)
- [Data and persistence](#data-and-persistence)
- [Security notes](#security-notes)
- [Development](#development)
- [Licence](#licence)
- [Acknowledgements](#acknowledgements)

---

## Features

**Table setup**
- Upload, rename, and delete battlemaps
- Grid overlay with a **live pixel-width slider** — drag until it lines up with the art, no maths
- Grid offset, colour, and opacity, remembered per map
- Multiple *scenes* prepped in advance, switched with one click

**Assets and tokens**
- Ships with the **Tom Cartos Open License Asset Bundle** — 800 props, furnishings, and set dressing
- Assets carry their grid footprint (a `2x1` anvil occupies two squares by one), auto-detected on import
- Upload your own art; rename, resize by squares occupied, delete
- Place, drag, rotate, and layer tokens; positions survive a restart

**Running the game**
- **GM screen** with full control, plus a chrome-free **display window** for the second monitor
- **Fog of war** painted by the GM and enforced *server-side* — hidden map data is never sent to players
- **Chat with dice**, rolled on the server. Public rolls for the table, private rolls for you and the GM only
- Read-only **Obsidian vault** browsing for players, with a folder allow-list so your spoilers stay yours
- **Player notes**, private and shared

**Deployment**
- Runs on Windows, macOS, and Linux
- Local, LAN, host-created Wi-Fi hotspot, direct internet, or cloud VPS
- Single-file executables planned — see the roadmap

---

## Requirements

- **Python 3.10 or newer** (developed against 3.12)
- A modern browser (Chrome, Edge, Firefox, or Safari)
- ~600 MB of disk space if you install the full Tom Cartos asset bundle

Nothing else. No Node, no database server, no Docker required.

---

## Quick start

**Windows (PowerShell)**

```powershell
.\scripts\setup.ps1
```

**macOS / Linux**

```bash
./scripts/setup.sh
```

Setup creates a virtual environment, installs dependencies, initialises the database, and offers to install the map assets. Then start the server:

```bash
./scripts/start.sh
```

```powershell
.\scripts\start.ps1
```

On Windows you can also just **double-click `start.bat`** in the EzVTT folder, and `stop.bat` when you're finished. They take the same options as the scripts they call:

```
start.bat -Mode lan -Port 9000
```

Your browser opens on the **GM screen**. Click **Open Display Window**, drag that window to your second monitor, and press `F11` for fullscreen. That's the table view.

On first run you'll be asked to create a master admin account. See [Beta bypass](#beta-bypass) if you'd rather skip that while testing.

---

## Run modes

Pass `--mode` to the start script. Every mode serves the same application on the same port; they differ in what the server binds to and how much it nags you about security.

| Mode | Binds | Who can reach it | Use when |
|---|---|---|---|
| `local` | `127.0.0.1` | This machine only | You and your players are in the same room, one screen each |
| `lan` | `0.0.0.0` | Your home/venue network | Players bring phones or laptops to the table |
| `hotspot` | `0.0.0.0` + host AP | Devices joined to your machine's Wi-Fi | No network available — see the warning below |
| `internet` | `0.0.0.0` | Anyone with your IP | Remote players, hosted from your own machine |
| `vps` | `0.0.0.0` behind a proxy | Anyone with your domain | Always-on hosting in the cloud |

```bash
./scripts/start.sh --mode lan
./scripts/start.sh --mode internet --port 8080
```

In `lan` and `hotspot` modes the console and the GM screen both show a **join URL and a QR code** so players can point a phone camera at the screen instead of typing an IP address.

### ⚠️ Hotspot mode may not work on your machine

`hotspot` mode asks your operating system to turn your Wi-Fi adapter into an access point. **This frequently fails, through no fault of EzVTT**, because of platform and device restrictions that have tightened considerably in recent years:

- **Windows** — the old `netsh wlan start hostednetwork` interface has been removed from most modern Wi-Fi drivers. The replacement, Mobile Hotspot, requires adapter support and often refuses to start while you are connected to certain networks or to a VPN.
- **macOS** — Internet Sharing cannot be enabled reliably from the command line, and macOS will not share a Wi-Fi connection *over* Wi-Fi. Expect to enable it by hand in System Settings, if at all.
- **Linux** — works reasonably well *if* you have NetworkManager and an AP-capable adapter (`nmcli device wifi hotspot`). Many USB adapters are not AP-capable.

Even when the access point starts, **client devices may still fail to connect or stay connected**:

- Phones with **private / randomised Wi-Fi addresses** may be treated as new clients on every reconnect.
- Android and iOS detect that the hotspot has **no internet access** and will often switch silently back to mobile data. Players may need to disable "auto-switch to mobile data" or approve a "stay connected" prompt.
- Some adapters enable **client isolation**, which blocks devices from reaching your machine even once joined.
- Corporate or school-managed laptops usually block hotspot creation outright by policy.

**Hotspot mode is best-effort.** If it fails, EzVTT tells you why and falls back to `lan` mode. Where there is any existing network — even a phone's mobile hotspot that everyone joins — `lan` mode is more reliable and is the recommended choice.

### ⚠️ Internet mode exposes your machine

`internet` mode makes EzVTT reachable from the public internet. Before using it:

- You will need to **forward a port** on your router to this machine. EzVTT prints the address and port to forward.
- **Authentication is forced on** and the beta bypass is disabled automatically. This is not configurable.
- Traffic is **unencrypted HTTP** unless you put a TLS-terminating reverse proxy in front. Passwords and session cookies travel in clear text over plain HTTP. Use a proxy, or use `vps` mode.
- Your home IP address becomes known to everyone you share it with.

For anything beyond an occasional game, `vps` mode behind a proxy with a real certificate is the better answer. See [`docs/RUN_MODES.md`](docs/RUN_MODES.md) for nginx and systemd examples.

---

## Scripts

Every script exists in a PowerShell (`.ps1`) and a POSIX shell (`.sh`) version and behaves identically.

On Windows, `start.bat` and `stop.bat` sit in the top level of the folder and can be double-clicked. They pass their arguments through to the scripts below.

| Script | What it does |
|---|---|
| `setup` | Creates the venv, installs pinned dependencies, initialises the database, optionally installs assets |
| `start` | Launches the server. Accepts `--mode`, `--port`, `--host` |
| `stop` | Graceful shutdown via the PID file — finishes in-flight requests and closes the database cleanly |
| `kill` | Force termination. Use only when `stop` will not work |
| `fetch-assets` | Installs or refreshes the Tom Cartos asset bundle |

The running process records its PID at `data/run/ezvtt.pid`. `stop` and `kill` read it from there, so they work from any shell.

Scripts deliberately launch the server in a **visible** console window. Hidden background window spawning is a well-known antivirus heuristic trigger and is not worth the cosmetic gain.

---

## Map assets

EzVTT ships with support for the **Tom Cartos Open License Asset Bundle** — 800 hand-drawn props, furnishings, and scenery pieces, generously released for free by Tom Cartos.

Install them with:

```bash
./scripts/fetch-assets.sh
```

```powershell
.\scripts\fetch-assets.ps1
```

Assets are installed to `assets/bundled/` and are not tracked in git — the bundle is over 500 MB, which would make cloning this repository unpleasant.

**A note on how EzVTT treats these files.** Tom's Open Map License does not permit editing, cropping, or adding to the assets. EzVTT therefore **never modifies a bundled Cartos image on disk.** When you "resize" one of these assets, EzVTT records how many grid squares it should occupy and scales the *unmodified original* at render time. Only art you upload yourself is ever transformed. Please respect these terms if you contribute to EzVTT.

---

## Data and persistence

Everything that should survive a restart lives under `data/`:

```
data/
├── ezvtt.db        SQLite — accounts, maps, scenes, tokens, fog, notes, chat
├── maps/           uploaded battlemaps
├── assets/         uploaded assets
├── thumbs/         generated thumbnails
└── run/ezvtt.pid   PID of the running server
```

Grid sizes, token positions, fog of war, and notes are written as they change. Close EzVTT mid-combat, reopen it, and the table is exactly as you left it.

To back up a campaign, copy the `data/` directory. To start fresh, delete `data/ezvtt.db` — you will be prompted to create a master admin again on next launch.

---

## Security notes

### Beta bypass

While EzVTT is in beta, the login screen carries a **"Skip login (beta)"** button that signs you straight in as GM, so you are not re-authenticating on every restart during development.

- It is **on by default** in `local` mode.
- It is **automatically and unconditionally disabled** in `internet` and `vps` modes.
- A persistent banner is shown across the UI whenever it is active, so you cannot forget it is on.
- It will be removed when EzVTT reaches 1.0.

Turn it off manually at any time from **Admin → Settings**, or with `--no-bypass`.

### Other notes

- Passwords are hashed with `scrypt` and a per-user salt. Plaintext passwords are never stored or logged.
- Fog of war is enforced on the server. Map regions a player has not been shown are **not transmitted to their browser**, so opening developer tools reveals nothing.
- Dice are rolled on the server. A modified client cannot fake a result.
- Uploads are validated by content, restricted to image types, and served through a path-traversal-guarded handler.
- Obsidian vault access is read-only, confined to the configured vault root, and further restricted by a folder allow-list.

Please report security issues privately rather than through a public issue.

---

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
.venv/bin/python -m ezvtt --mode local --reload
.venv/bin/python -m pytest
```

Orientation for contributors — and for AI coding assistants — is in [`CLAUDE.md`](CLAUDE.md). Architectural decisions and their rationale are recorded in [`docs/DECISIONS.md`](docs/DECISIONS.md); read it before proposing a change to the stack.

Design constraints worth knowing up front:

- **One process, one port.** Authentication, WebSockets, static files, and persistence share a single FastAPI application.
- **The server is authoritative.** Clients send intents; the server checks permission, writes to SQLite, and broadcasts the result.
- **No frontend build step.** ES modules and Canvas 2D, served as written.
- **No CDN.** EzVTT works with no internet connection at all.
- **A tight dependency budget.** Every dependency is a line in the acknowledgements below and a risk to single-file packaging.

---

## Licence

EzVTT's **source code** is licensed under the **Apache License, Version 2.0**.

Copyright 2026 EzVTT Contributors.

- Full licence text: [`LICENSE`](LICENSE) — or <https://www.apache.org/licenses/LICENSE-2.0>
- Attribution notices: [`NOTICE`](NOTICE)

**The Apache Licence covers the code only.** Artwork distributed with or downloaded by EzVTT is licensed separately by its creator and is **not** relicensed by us — see the acknowledgements below.

---

## Acknowledgements

### Tom Cartos

**Thank you to [Tom Cartos](https://www.tomcartos.com/) for releasing the Tom Cartos Open License Asset Bundle free of charge.**

800 pieces of beautiful, consistent, ready-to-use map art is an extraordinary thing to give away, and it is the single largest reason EzVTT can deliver on its promise of getting a map on the table in under a minute. A new GM can install this program and have a furnished tavern in front of their players immediately, without spending a penny or drawing a line. That is entirely down to Tom's generosity.

These assets remain the work and property of Tom Cartos and are provided under **Tom's Open Map License**:

> ### 📜 [Tom's Open Map License — read it in full](https://www.tomcartos.com/toms-open-map-license)

They are **not** covered by EzVTT's Apache Licence, and EzVTT does not and cannot relicense or sublicense them. You receive them under Tom's licence directly from Tom Cartos. **If you redistribute EzVTT or build on it, read that licence and comply with it yourself** — it governs attribution, modification, and permitted use, and it is the authoritative statement of those terms rather than anything we could summarise here.

One consequence is baked into EzVTT's design and worth knowing as a contributor: **the licence does not permit editing these assets, so EzVTT never modifies a bundled Cartos image on disk.** Grid-footprint sizing is applied as a display-time transform over the unmodified original. Please preserve that behaviour.

The attribution Tom asks for:

*Cartography and map assets by Tom Cartos — <https://www.tomcartos.com/>*

Please consider supporting his work at <https://www.tomcartos.com/>.

### Open source software

EzVTT is built on the following projects. Each licence is linked at source — please read them there rather than relying on the identifier in this table.

| Project | Licence | Used for |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE) | Web framework |
| [Starlette](https://www.starlette.io/) | [BSD 3-Clause](https://github.com/encode/starlette/blob/master/LICENSE.md) | ASGI toolkit underpinning FastAPI |
| [Pydantic](https://docs.pydantic.dev/) | [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE) | Request and settings validation |
| [Uvicorn](https://www.uvicorn.org/) | [BSD 3-Clause](https://github.com/encode/uvicorn/blob/master/LICENSE.md) | ASGI server |
| [websockets](https://websockets.readthedocs.io/) | [BSD 3-Clause](https://github.com/python-websockets/websockets/blob/main/LICENSE) | Live table synchronisation |
| [python-multipart](https://github.com/Kludex/python-multipart) | [Apache 2.0](https://github.com/Kludex/python-multipart/blob/master/LICENSE.txt) | Multipart form and file upload parsing |
| [Jinja2](https://jinja.palletsprojects.com/) | [BSD 3-Clause](https://github.com/pallets/jinja/blob/main/LICENSE.txt) | HTML templating |
| [MarkupSafe](https://markupsafe.palletsprojects.com/) | [BSD 3-Clause](https://github.com/pallets/markupsafe/blob/main/LICENSE.txt) | Template escaping |
| [Pillow](https://python-pillow.org/) | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | Image dimensions and thumbnails |
| [Python-Markdown](https://python-markdown.github.io/) | [BSD 3-Clause](https://github.com/Python-Markdown/markdown/blob/master/LICENSE.md) | Obsidian vault rendering |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | [BSD 3-Clause](https://github.com/lincolnloop/python-qrcode/blob/main/LICENSE) | Join-by-phone QR codes |
| [h11](https://github.com/python-hyper/h11) | [MIT](https://github.com/python-hyper/h11/blob/master/LICENSE.txt) | HTTP/1.1 protocol handling |
| [AnyIO](https://anyio.readthedocs.io/) | [MIT](https://github.com/agronholm/anyio/blob/master/LICENSE) | Async compatibility layer |
| [Click](https://click.palletsprojects.com/) | [BSD 3-Clause](https://github.com/pallets/click/blob/main/LICENSE.txt) | Uvicorn's command line interface |
| [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) | [LGPL 2.1](https://github.com/python-zeroconf/python-zeroconf/blob/master/COPYING) | Optional `ezvtt.local` discovery |

**Installing from source does not redistribute these packages** — `setup` fetches them from PyPI on your machine, under their own licences, direct from their authors. Linking is therefore the appropriate acknowledgement here.

**Packaged builds are different.** The single-file executables planned for a later release *do* embed these packages, which triggers the bundled-licence requirements in the Apache, BSD, and MIT terms above. Those builds ship a generated `THIRD_PARTY_LICENSES.md` produced by [`scripts/gen_third_party_licenses.py`](scripts/gen_third_party_licenses.py), which reads each licence byte-exact from the installed package's own metadata rather than transcribing it. `python-zeroconf` is LGPL 2.1 and is used only as an unmodified, optional, dynamically imported dependency that can be omitted without loss of core functionality.

### Everyone else

Thanks to the Python packaging and web communities, and to the game masters who tested this at real tables and told us, bluntly, which parts were too slow.

---

<sub>EzVTT is an independent project. It is not affiliated with, endorsed by, or sponsored by Tom Cartos, Wizards of the Coast, or any game publisher.</sub>
