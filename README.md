# EzVTT

A cross-platform **Virtual Tabletop**. A Game Master runs it locally or on a VPS;
players connect over the internet through their browser.

## Architecture

```
   Players / GM (browsers)
            │  HTTPS / WSS  (one public port)
            ▼
   ┌─────────────────────────────┐
   │  JAVA EDGE + GAME SERVER     │  public :8080  (security boundary)
   │  • TLS, auth, GM/player AuthZ │
   │  • WebSocket hub (play space) │
   │  • reverse-proxies pages ─┐   │
   └───────────────────────────│───┘
              localhost only    │
   ┌──────────────────────────▼──┐
   │  DJANGO UI APP  127.0.0.1:8000 │
   │  • main / wiki / play / admin  │
   │  • trusts Java identity header │
   └────────────────────────────────┘
```

- **Java** is the only internet-facing process: TLS, auth, and the real-time
  WebSocket hub for the shared play space.
- **Django** renders the browser UI and is reachable only from localhost, behind
  Java. It trusts the `X-EzVTT-Role` / `X-EzVTT-User` headers Java injects.

## Run the demo

Requires a JDK (`java`/`javac`) and Python 3.

```powershell
# Windows
./run.ps1
```
```bash
# macOS / Linux
./run.sh
```

Then open two windows:
- GM:     <http://localhost:8080/play?role=gm>
- Player: <http://localhost:8080/play>

The GM clicks a grid cell to move the token; the player's view updates live but
cannot move it (rejected server-side by Java).

## Demo scope

This is a thin vertical slice to prove the topology. It deliberately omits TLS,
real login/auth, HMAC-signed identity, the Markdown wiki, and persistence — those
are the next layers. See `.claude/memory/project-overview.md` for the locked
architecture decisions.
