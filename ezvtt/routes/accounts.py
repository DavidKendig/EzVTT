"""First-run setup, login, logout, and the beta bypass.

The bypass signs in as a real account with a real session rather than a
synthetic principal. That keeps one authentication path to secure: everything
downstream -- route guards, WebSocket handshakes, the admin screen -- treats a
bypassed GM exactly like any other, and the run mode decides whether the button
exists at all.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import auth, db
from ..auth import Role

log = logging.getLogger("ezvtt.accounts")

router = APIRouter(tags=["accounts"])


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #

def _set_session_cookie(response: Response, value: str, secure: bool) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        value,
        max_age=auth.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,          # JavaScript must never be able to read it
        samesite="lax",         # sent on top-level navigation, not on cross-site posts
        secure=secure,
        path="/",
    )


def csrf_for(request: Request) -> tuple[str, bool]:
    """The CSRF token for this request, and whether the cookie must be set.

    Resolved before rendering so the token can go into the form, and the cookie
    attached to the finished response afterwards.
    """
    existing = request.cookies.get(auth.CSRF_COOKIE)
    if existing:
        return existing, False
    return auth.new_csrf_token(), True


def attach_csrf_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        auth.CSRF_COOKIE,
        token,
        max_age=auth.SESSION_TTL_DAYS * 24 * 3600,
        # Deliberately readable by the page that renders the form. The
        # protection comes from a cross-origin attacker being unable to read it,
        # not from hiding it from our own JavaScript.
        httponly=False,
        samesite="lax",
        secure=request.state.settings.policy.secure_cookies,
        path="/",
    )


def _check_csrf(request: Request, submitted: str) -> bool:
    return auth.csrf_valid(request.cookies.get(auth.CSRF_COOKIE), submitted)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _render(request: Request, template: str, **context) -> HTMLResponse:
    from ..app import templates

    settings = request.state.settings
    token, needs_cookie = csrf_for(request)

    response = templates.TemplateResponse(
        request, template,
        {
            "request": request,
            "mode": settings.mode.value,
            "csrf_token": token,
            "bypass_available": settings.beta_bypass_available
                                and db.get_flag("beta_bypass", default=True),
            "campaign_name": db.get_setting("campaign_name", "A New Campaign"),
            **context,
        },
    )
    if needs_cookie:
        attach_csrf_cookie(request, response, token)
    return response


# --------------------------------------------------------------------------- #
# First run
# --------------------------------------------------------------------------- #

@router.get("/setup", include_in_schema=False)
async def setup_form(request: Request):
    if not db.is_first_run():
        return RedirectResponse("/", status_code=303)
    return _render(request, "setup.html")


@router.post("/setup", include_in_schema=False)
async def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
):
    if not db.is_first_run():
        # Someone else completed setup while this form was open. Creating a
        # second admin from an unauthenticated route would be a way in.
        return RedirectResponse("/login", status_code=303)

    if not _check_csrf(request, csrf_token):
        return _render(request, "setup.html", error="That form expired. Try again.")

    if password != confirm:
        return _render(request, "setup.html", error="The passwords do not match.",
                       username=username, display_name=display_name)

    try:
        user_id = auth.create_user(username, password, Role.ADMIN, display_name)
    except ValueError as exc:
        return _render(request, "setup.html", error=str(exc),
                       username=username, display_name=display_name)

    db.set_flag("first_run_complete", True)
    log.info("Master admin %r created", username)

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(
        response,
        auth.create_session(user_id, _client_ip(request), _user_agent(request)),
        request.state.settings.policy.secure_cookies,
    )
    return response


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #

@router.get("/login", include_in_schema=False)
async def login_form(request: Request, next: str = "/"):
    if db.is_first_run():
        return RedirectResponse("/setup", status_code=303)
    if request.state.principal.is_authenticated:
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", next=_safe_next(next))


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    destination = _safe_next(next)

    if not _check_csrf(request, csrf_token):
        return _render(request, "login.html", error="That form expired. Try again.",
                       next=destination)

    principal = auth.authenticate(username, password)
    if principal is None:
        log.warning("Failed sign-in for %r from %s", username, _client_ip(request))
        # One message for both causes: naming which was wrong tells an attacker
        # which usernames exist.
        return _render(request, "login.html", next=destination,
                       error="That username and password do not match.",
                       username=username)

    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(
        response,
        auth.create_session(principal.user_id, _client_ip(request), _user_agent(request)),
        request.state.settings.policy.secure_cookies,
    )
    return response


@router.post("/login/bypass", include_in_schema=False)
async def login_bypass(request: Request, csrf_token: str = Form("")):
    """Sign in as the beta GM account without a password.

    Refused outright by run modes that face the network -- checked here as well
    as in the template, because a hidden button is not a control.
    """
    settings = request.state.settings
    if not settings.beta_bypass_available or not db.get_flag("beta_bypass", default=True):
        return _render(request, "login.html",
                       error="The beta bypass is disabled in this run mode.")

    if not _check_csrf(request, csrf_token):
        return _render(request, "login.html", error="That form expired. Try again.")

    user_id = auth.ensure_bypass_account()
    log.warning("Beta bypass sign-in from %s", _client_ip(request))

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(
        response,
        auth.create_session(user_id, _client_ip(request), _user_agent(request)),
        settings.policy.secure_cookies,
    )
    return response


@router.post("/logout", include_in_schema=False)
async def logout(request: Request, csrf_token: str = Form("")):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse("/", status_code=303)

    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _safe_next(destination: str) -> str:
    """Only allow redirects to our own paths.

    Without this, /login?next=https://evil.example is an open redirect: a
    convincing phishing link that genuinely starts on the GM's own server.
    """
    if not destination.startswith("/") or destination.startswith("//"):
        return "/"
    return destination


def _client_ip(request: Request) -> str:
    if request.state.settings.policy.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")
