"""Admin screen: accounts and server settings.

Admin-only. Guarded at the router so a new endpoint here cannot ship without a
check, and every mutating form carries a CSRF token.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, campaign, db
from ..auth import Role
from ..deps import require_admin
from .accounts import _check_csrf, attach_csrf_cookie, csrf_for

log = logging.getLogger("ezvtt.admin")

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


def _render(request: Request, **context) -> HTMLResponse:
    from ..app import templates

    settings = request.state.settings
    token, needs_cookie = csrf_for(request)

    response = templates.TemplateResponse(
        request, "admin.html",
        {
            "request": request,
            "principal": request.state.principal,
            "version": request.app.version,
            "mode": settings.mode.value,
            "csrf_token": token,
            "users": auth.list_users(),
            "bypass_enabled": db.get_flag("beta_bypass", default=True),
            "bypass_allowed": settings.policy.allow_beta_bypass,
            "bypass_username": auth.BYPASS_USERNAME,
            "campaign_name": db.get_setting("campaign_name", "A New Campaign"),
            "bypass_active": auth.is_bypass_principal(request.state.principal),
            "pending_import": campaign.pending(),
            **context,
        },
    )
    if needs_cookie:
        attach_csrf_cookie(request, response, token)
    return response


@router.get("", include_in_schema=False)
async def admin_page(request: Request, ok: str = "", error: str = ""):
    return _render(request, ok=ok, error=error)


def _back(ok: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in (("ok", ok), ("error", error)) if v})
    return RedirectResponse(f"/admin?{query}" if query else "/admin", status_code=303)


@router.post("/users", include_in_schema=False)
async def create_account(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("player"),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    try:
        auth.create_user(username, password, role, display_name)
    except ValueError as exc:
        return _back(error=str(exc))

    log.info("Account %r created as %s", username, role)
    return _back(ok=f"Created {username}.")


@router.post("/users/{user_id}/password", include_in_schema=False)
async def reset_password(
    request: Request, user_id: int,
    password: str = Form(...), csrf_token: str = Form(""),
):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    try:
        auth.set_password(user_id, password)
    except ValueError as exc:
        return _back(error=str(exc))

    user = auth.get_user(user_id)
    # set_password signs the account out everywhere, which is the point of
    # resetting it -- say so rather than leaving the admin to wonder.
    return _back(ok=f"Password reset for {user['username']}; signed out everywhere.")


@router.post("/users/{user_id}/role", include_in_schema=False)
async def change_role(
    request: Request, user_id: int,
    role: str = Form(...), csrf_token: str = Form(""),
):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    try:
        auth.set_role(user_id, role)
    except ValueError as exc:
        return _back(error=str(exc))

    user = auth.get_user(user_id)
    return _back(ok=f"{user['username']} is now {role}.")


@router.post("/users/{user_id}/active", include_in_schema=False)
async def toggle_active(
    request: Request, user_id: int,
    active: str = Form("0"), csrf_token: str = Form(""),
):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    wants_active = active in ("1", "true", "on", "yes")
    try:
        auth.set_active(user_id, wants_active)
    except ValueError as exc:
        return _back(error=str(exc))

    user = auth.get_user(user_id)
    return _back(ok=f"{user['username']} {'enabled' if wants_active else 'disabled'}.")


@router.post("/users/{user_id}/delete", include_in_schema=False)
async def remove_account(request: Request, user_id: int, csrf_token: str = Form("")):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    if user_id == request.state.principal.user_id:
        # Deleting the account you are signed in as is almost never intended and
        # is unrecoverable if it was the last admin.
        return _back(error="You cannot delete the account you are signed in as.")

    user = auth.get_user(user_id)
    try:
        if not auth.delete_user(user_id):
            return _back(error="No such account.")
    except ValueError as exc:
        return _back(error=str(exc))

    log.info("Account %r deleted", user["username"])
    return _back(ok=f"Deleted {user['username']}.")


@router.post("/settings/bypass", include_in_schema=False)
async def set_bypass(request: Request, enabled: str = Form("0"), csrf_token: str = Form("")):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    settings = request.state.settings
    wants = enabled in ("1", "true", "on", "yes")

    if wants and not settings.policy.allow_beta_bypass:
        return _back(error=f"The bypass cannot be enabled in {settings.mode.value} mode.")

    db.set_flag("beta_bypass", wants)
    log.warning("Beta bypass %s", "enabled" if wants else "disabled")
    return _back(ok=f"Beta bypass {'enabled' if wants else 'disabled'}.")


@router.post("/settings/campaign", include_in_schema=False)
async def set_campaign_name(
    request: Request, campaign_name: str = Form(...), csrf_token: str = Form(""),
):
    if not _check_csrf(request, csrf_token):
        return _back(error="That form expired. Try again.")

    name = campaign_name.strip()[:120]
    if not name:
        return _back(error="The campaign needs a name.")

    db.set_setting("campaign_name", name)
    return _back(ok="Campaign name updated.")


@router.post("/users/{user_id}/suggest-password", include_in_schema=False)
async def suggest_password(request: Request, user_id: int):
    """A generated passphrase, so an admin creating five player accounts is not
    inventing five passwords."""
    return {"password": secrets.token_urlsafe(12)}


__all__ = ["router", "Role"]
