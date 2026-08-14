"""Shared route guards.

One place that decides whether a caller may do something, so a new route cannot
quietly ship without a check by forgetting to copy one in. Every guard reads the
principal the middleware resolved from the session cookie -- never anything in
the request body.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import Principal, Role


class Forbidden(Exception):
    """Raised by the require_* helpers. Carries a user-safe message."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message = message
        self.status = status


def principal(request: Request) -> Principal:
    return request.state.principal


def require(request: Request, minimum: Role) -> Principal:
    who = principal(request)
    if not who.has_at_least(minimum):
        # 401 when there is nobody signed in, 403 when someone is but lacks the
        # role. The distinction tells a client whether logging in would help.
        if who.role is Role.ANON:
            raise Forbidden("Sign in to do that.", status=401)
        raise Forbidden(_message_for(minimum))
    return who


def _message_for(minimum: Role) -> str:
    return {
        Role.ADMIN: "Only an administrator can do that.",
        Role.GM: "Only the GM can do that.",
        Role.PLAYER: "Sign in to do that.",
    }.get(minimum, "You do not have permission to do that.")


def require_player(request: Request) -> Principal:
    return require(request, Role.PLAYER)


def require_gm(request: Request) -> Principal:
    return require(request, Role.GM)


def require_admin(request: Request) -> Principal:
    return require(request, Role.ADMIN)


def forbidden_response(exc: Forbidden) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)
