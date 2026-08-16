"""FastAPI application factory.

One process serves everything: pages, API, media, and the WebSocket hub. See
ADR-001.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import quote

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, assets, auth, config, db
from .config import Settings
from .deps import Forbidden, forbidden_response, require_gm
from .hub import MAX_MESSAGE_BYTES, hub
from .routes import accounts as account_routes
from .routes import admin as admin_routes
from .routes import assets as asset_routes
from .routes import campaign as campaign_routes
from .routes import handouts as handout_routes
from .routes import maps as maps_routes
from .routes import media_files
from .routes import network as network_routes
from .routes import scenes as scene_routes
from .routes import wiki as wiki_routes

log = logging.getLogger("ezvtt")

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


class RevalidatingStatics(StaticFiles):
    """Serve static files with ``Cache-Control: no-cache``.

    Starlette sends an ETag and Last-Modified but no Cache-Control, which leaves
    browsers free to apply heuristic caching and keep running yesterday's
    JavaScript against today's server. That shows up as "I updated EzVTT and
    nothing changed", and it is genuinely hard to diagnose from the outside.

    ``no-cache`` does not mean "do not store" -- it means "revalidate before
    reusing". With the ETag already present that costs one conditional request
    per file and almost always answers 304, which is nothing on a LAN and free
    on loopback.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _wants_json(request: Request) -> bool:
    """Whether to answer with JSON rather than a redirect.

    fetch() sets this header; a browser following a link does not. It decides
    whether an unauthenticated caller gets a 401 they can handle or a redirect
    to the login page.
    """
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _principal_from_cookies(cookies: dict, settings: Settings) -> auth.Principal:
    """Identify a caller from their session cookie.

    Takes cookies rather than a Request because WebSocket handshakes need the
    same resolution and Starlette's HTTP middleware does not run for them. A
    socket that skipped this check would be an unauthenticated back door into
    the same intents the REST routes guard.

    Phase 0 recognises real sessions and the beta bypass. Phase 3 adds the login
    screens that create them; the guards written against this keep working
    unchanged.
    """
    raw = cookies.get(auth.SESSION_COOKIE)
    if raw:
        token = auth.unsign(raw)
        if token:
            principal = auth.principal_for_token(token)
            if principal is not None:
                # The bypass account is an ordinary session; flag it only so the
                # UI can keep warning that nobody actually authenticated.
                if auth.is_bypass_principal(principal):
                    return replace(principal, via_bypass=True)
                return principal

    return auth.ANONYMOUS


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    applied = db.initialise()
    if applied:
        log.info("Applied migrations: %s", ", ".join(applied))

    purged = auth.purge_expired_sessions()
    if purged:
        log.info("Purged %d expired session(s)", purged)

    # Index any artwork installed since the last run. Idempotent, and a failure
    # here must not stop the server: a GM with no asset bundle can still upload
    # their own maps and play.
    try:
        result = assets.import_bundled()
        if result["added"]:
            log.info("Indexed %d new bundled asset(s)", result["added"])
    except Exception:
        log.exception("Could not index bundled assets; continuing without them")

    # A mode that forbids the bypass must not leave it enabled in the database
    # from an earlier local run -- otherwise exposing the server to the internet
    # would silently carry a one-click GM login with it.
    if not settings.policy.allow_beta_bypass and db.get_flag("beta_bypass"):
        db.set_flag("beta_bypass", False)
        log.warning(
            "Beta login bypass disabled: not permitted in %s mode.", settings.mode.value
        )

    log.info("EzVTT %s ready on %s (%s mode)",
             __version__, settings.bind_description, settings.mode.value)

    yield

    db.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    config.ensure_directories()

    app = FastAPI(
        title="EzVTT",
        version=__version__,
        description="A virtual tabletop built for getting a map on the table fast.",
        lifespan=_lifespan,
        # The interactive docs are a developer tool, not something to expose on a
        # server the public can reach.
        docs_url="/api/docs" if settings.mode in (config.RunMode.LOCAL,) else None,
        redoc_url=None,
    )
    app.state.settings = settings

    app.mount(
        "/static",
        RevalidatingStatics(directory=str(config.STATIC_DIR)),
        name="static",
    )

    app.include_router(account_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(maps_routes.router)
    app.include_router(scene_routes.router)
    app.include_router(asset_routes.router)
    app.include_router(handout_routes.router)
    app.include_router(campaign_routes.router)
    app.include_router(network_routes.router)
    app.include_router(wiki_routes.router)
    app.include_router(wiki_routes.notes_router)
    app.include_router(media_files.router)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        # API callers get JSON; a browser following a link gets sent to login.
        if request.url.path.startswith("/api/") or _wants_json(request):
            return forbidden_response(exc)
        if request.state.principal.role is auth.Role.ANON:
            return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
        return forbidden_response(exc)

    # Paths that must stay reachable when nobody is signed in, or the setup and
    # login pages could never be rendered.
    #
    # Matched by prefix, not equality: /login/bypass is part of signing in and
    # must be reachable by someone who is, by definition, not signed in yet.
    OPEN_PREFIXES = ("/login", "/setup", "/health", "/static/")

    def _is_open(path: str) -> bool:
        return any(
            path == prefix or path.startswith(prefix.rstrip("/") + "/")
            for prefix in OPEN_PREFIXES
        )

    @app.middleware("http")
    async def attach_principal(request: Request, call_next):
        request.state.settings = settings
        request.state.principal = _principal_from_cookies(request.cookies, settings)

        path = request.url.path
        is_open = _is_open(path)

        # Until a master admin exists, everything funnels to the setup wizard.
        # Checked here rather than per-route so a route added later cannot be
        # reachable on a fresh install by accident.
        #
        # is_open is tested first deliberately: is_first_run() is a SQL COUNT,
        # and as the left operand it ran on every static file and thumbnail
        # request -- sixty-odd wasted queries just to open the asset library.
        if not is_open and db.is_first_run():
            if _wants_json(request) or path.startswith("/api/"):
                return JSONResponse(
                    {"error": "EzVTT is not set up yet."}, status_code=503
                )
            return RedirectResponse("/setup", status_code=303)

        # Once set up, the pages themselves require a sign-in. The API enforces
        # its own finer-grained role guards on top of this.
        #
        # The WebSocket is NOT covered here -- Starlette runs http middleware
        # only for http scopes -- so /ws checks authentication itself in
        # board_socket. Do not add a /ws special case to this block; it would
        # read as protection that never actually runs.
        if not is_open and not request.state.principal.is_authenticated:
            if path.startswith("/api/") or _wants_json(request):
                return JSONResponse({"error": "Sign in to do that."}, status_code=401)
            return RedirectResponse(f"/login?next={quote(path)}", status_code=303)

        return await call_next(request)

    def _page_context(request: Request) -> dict:
        principal: auth.Principal = request.state.principal
        token, _ = account_routes.csrf_for(request)
        return {
            "request": request,
            "version": __version__,
            "mode": settings.mode.value,
            "principal": principal,
            "bypass_active": principal.via_bypass,
            "csrf_token": token,
            "campaign_name": db.get_setting("campaign_name", "A New Campaign"),
        }

    def _page(request: Request, template: str, **extra):
        """Render a page, making sure the CSRF cookie exists for its forms."""
        context = _page_context(request)
        context.update(extra)
        response = templates.TemplateResponse(request, template, context)
        if not request.cookies.get(auth.CSRF_COOKIE):
            account_routes.attach_csrf_cookie(request, response, context["csrf_token"])
        return response

    # ----------------------------------------------------------------- health

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        """Liveness probe. Also used by the start scripts to confirm readiness."""
        return JSONResponse(
            {
                "status": "ok",
                "app": "EzVTT",
                "version": __version__,
                "mode": settings.mode.value,
                "first_run": db.is_first_run(),
            }
        )

    # -------------------------------------------------------------- websocket

    @app.websocket("/ws")
    async def board_socket(websocket: WebSocket, surface: str = "play"):
        """The live table connection.

        Role is resolved from the session cookie at handshake time and fixed for
        the life of the socket. Nothing in a later message can change it -- a
        client claiming to be the GM in a payload is ignored.
        """
        principal = _principal_from_cookies(websocket.cookies, settings)

        # Starlette runs HTTP middleware only for HTTP scopes, so the sign-in
        # gate that covers every page does NOT cover this handshake. Without
        # this check an unauthenticated socket is handed the player snapshot --
        # map, scene, campaign name, and every visible token position -- on a
        # server whose pages all require a login. Refuse before accepting.
        if not principal.is_authenticated:
            await websocket.close(code=1008, reason="Sign in to join the table.")
            return

        if surface not in ("gm", "display", "play"):
            surface = "play"
        # Only a GM may drive the shared display; otherwise a player opening
        # /display would be handed the GM's unfiltered view of the table.
        if surface in ("gm", "display") and not principal.is_gm:
            surface = "play"

        connection = await hub.connect(websocket, principal, surface)
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw) > MAX_MESSAGE_BYTES:
                    await hub.send(connection, {
                        "type": "error", "message": "Message too large."
                    })
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await hub.send(connection, {
                        "type": "error", "message": "Malformed message."
                    })
                    continue
                await hub.handle(connection, message)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("WebSocket error for %s", principal.username)
        finally:
            await hub.disconnect(connection)

    # ------------------------------------------------------------------ pages

    @app.get("/", include_in_schema=False)
    async def home(request: Request):
        # Players landing on the root get the player view rather than a
        # permission error -- the URL a GM shares is usually just the host.
        if not request.state.principal.is_gm:
            return RedirectResponse("/play", status_code=303)
        return _page(request, "gm.html", page_title="GM Screen")

    @app.get("/display", include_in_schema=False)
    async def display_window(request: Request):
        require_gm(request)
        return _page(request, "display.html", page_title="Display")

    @app.get("/play", include_in_schema=False)
    async def player_view(request: Request):
        return _page(request, "play.html", page_title="Play")

    return app
