"""Password hashing, session tokens, and role resolution.

Phase 0 provides the identity *plumbing* -- every request resolves to a
``Principal`` carrying a role, and route guards are written against that from
the start. Login screens and account management arrive in Phase 3; wiring roles
in first avoids retrofitting permission checks through finished code, which is
where authorisation bugs come from.

Hashing is stdlib ``scrypt`` and session signing is stdlib ``hmac`` -- no
compiled dependency. See ADR-007.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from enum import Enum

from . import db

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

# scrypt cost. n=2**15 with r=8 needs ~32 MB per hash, which is a comfortable
# fraction of a second on a laptop and unpleasant to attack in bulk. Stored
# per-user so these can be raised later without invalidating existing passwords.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

# scrypt needs maxmem >= roughly 128 * n * r; the default is too low for n=2**15.
_SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2


@dataclass(frozen=True)
class PasswordHash:
    hash_hex: str
    salt_hex: str
    n: int
    r: int
    p: int


def hash_password(password: str) -> PasswordHash:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return PasswordHash(digest.hex(), salt.hex(), SCRYPT_N, SCRYPT_R, SCRYPT_P)


def verify_password(password: str, stored: PasswordHash) -> bool:
    """Check a password against a stored hash, using that hash's own cost."""
    try:
        salt = bytes.fromhex(stored.salt_hex)
        expected = bytes.fromhex(stored.hash_hex)
    except ValueError:
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=stored.n,
        r=stored.r,
        p=stored.p,
        dklen=len(expected),
        maxmem=128 * stored.n * stored.r * 2,
    )
    # Constant-time: a plain == leaks how much of the digest matched via timing.
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

class Role(str, Enum):
    ANON = "anon"
    PLAYER = "player"
    GM = "gm"
    ADMIN = "admin"


# Ordered by authority so guards can express "at least a GM" rather than
# enumerating every acceptable role at each call site.
_RANK = {Role.ANON: 0, Role.PLAYER: 1, Role.GM: 2, Role.ADMIN: 3}


@dataclass(frozen=True)
class Principal:
    """Who is making a request."""

    user_id: int | None
    username: str
    display_name: str
    role: Role
    # True when this identity came from the beta bypass rather than a real
    # login. Surfaced in the UI so an unauthenticated session is never mistaken
    # for a real one.
    via_bypass: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self.role is not Role.ANON

    @property
    def is_gm(self) -> bool:
        return self.has_at_least(Role.GM)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    def has_at_least(self, minimum: Role) -> bool:
        return _RANK[self.role] >= _RANK[minimum]


ANONYMOUS = Principal(
    user_id=None, username="anonymous", display_name="Anonymous", role=Role.ANON
)

# The account the beta bypass signs in as. It is a real row with a real session
# rather than a synthetic principal, so there is exactly one authentication code
# path to reason about and to secure.
BYPASS_USERNAME = "beta-gm"


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

SESSION_COOKIE = "ezvtt_session"
SESSION_TTL_DAYS = 30

_SECRET_KEY_SETTING = "session_secret"


def session_secret() -> bytes:
    """The HMAC key for signing session cookies, generated once and persisted.

    Kept in the database rather than a config file so that a fresh install is
    secure by default with no key management step. Deleting it invalidates every
    outstanding session, which is the intended way to sign everyone out.
    """
    stored = db.get_setting(_SECRET_KEY_SETTING)
    if stored:
        return bytes.fromhex(stored)

    secret = secrets.token_bytes(32)
    db.set_setting(_SECRET_KEY_SETTING, secret.hex())
    return secret


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def sign(value: str) -> str:
    digest = hmac.new(session_secret(), value.encode("utf-8"), hashlib.sha256)
    return f"{value}.{digest.hexdigest()}"


def unsign(signed: str) -> str | None:
    """Recover a signed value, or None if the signature does not verify."""
    value, _, signature = signed.rpartition(".")
    if not value or not signature:
        return None
    expected = hmac.new(
        session_secret(), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


def principal_for_token(token: str) -> Principal | None:
    """Resolve a session token to a principal, or None if invalid or expired."""
    conn = db.connect()
    row = conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.role
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
          AND s.expires_at > datetime('now')
          AND u.is_active = 1
        """,
        (token,),
    ).fetchone()

    if row is None:
        return None

    return Principal(
        user_id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=Role(row["role"]),
    )


def purge_expired_sessions() -> int:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    conn.commit()
    return cursor.rowcount


def create_session(user_id: int, ip: str = "", user_agent: str = "") -> str:
    """Start a session. Returns the signed value to put in the cookie."""
    token = new_session_token()
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO sessions (token, user_id, expires_at, ip, user_agent)
        VALUES (?, ?, datetime('now', ?), ?, ?)
        """,
        (token, user_id, f"+{SESSION_TTL_DAYS} days", ip[:60], user_agent[:200]),
    )
    conn.execute(
        "UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (user_id,)
    )
    conn.commit()
    return sign(token)


def destroy_session(signed: str | None) -> None:
    if not signed:
        return
    token = unsign(signed)
    if not token:
        return
    conn = db.connect()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def destroy_sessions_for(user_id: int) -> int:
    """Sign a user out everywhere. Used on password reset and deactivation."""
    conn = db.connect()
    cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,30}$")
MIN_PASSWORD_LENGTH = 8


def validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise ValueError(
            "Usernames are 2 to 31 characters: letters, numbers, spaces, "
            "dots, underscores, and hyphens."
        )
    return username


def validate_password(password: str) -> str:
    # A length floor and nothing else. Composition rules push people towards
    # "Password1!" and away from the long passphrases that actually help.
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    return password


def create_user(
    username: str,
    password: str,
    role: Role | str,
    display_name: str = "",
    email: str = "",
) -> int:
    username = validate_username(username)
    validate_password(password)

    role = Role(role) if not isinstance(role, Role) else role
    if role is Role.ANON:
        raise ValueError("Cannot create an anonymous account.")

    stored = hash_password(password)
    conn = db.connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, display_name, email, pw_hash, pw_salt,
                 pw_n, pw_r, pw_p, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, (display_name or username).strip()[:60], email.strip()[:120],
             stored.hash_hex, stored.salt_hex, stored.n, stored.r, stored.p,
             role.value),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"An account called {username!r} already exists.") from None

    conn.commit()
    return cursor.lastrowid


def authenticate(username: str, password: str) -> Principal | None:
    """Check a username and password. Returns None on any failure.

    Deliberately does not distinguish "no such user" from "wrong password":
    telling an attacker which usernames exist is free reconnaissance.
    """
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username.strip(),)
    ).fetchone()

    if row is None:
        # Hash anyway so a missing account does not return measurably faster
        # than a wrong password, which would enumerate valid usernames.
        hash_password(password)
        return None

    stored = PasswordHash(
        row["pw_hash"], row["pw_salt"], row["pw_n"], row["pw_r"], row["pw_p"]
    )
    if not verify_password(password, stored):
        return None

    return Principal(
        user_id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=Role(row["role"]),
    )


def set_password(user_id: int, password: str) -> None:
    validate_password(password)
    stored = hash_password(password)
    conn = db.connect()
    conn.execute(
        """
        UPDATE users
        SET pw_hash = ?, pw_salt = ?, pw_n = ?, pw_r = ?, pw_p = ?
        WHERE id = ?
        """,
        (stored.hash_hex, stored.salt_hex, stored.n, stored.r, stored.p, user_id),
    )
    conn.commit()
    # Changing a password must not leave old sessions alive -- that is the whole
    # point of changing it after a suspected compromise.
    destroy_sessions_for(user_id)


def list_users() -> list[dict]:
    rows = db.connect().execute(
        """
        SELECT u.id, u.username, u.display_name, u.email, u.role, u.is_active,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM sessions s
                WHERE s.user_id = u.id AND s.expires_at > datetime('now')) AS sessions
        FROM users u
        ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'gm' THEN 1 ELSE 2 END,
                 u.username COLLATE NOCASE
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_user(user_id: int) -> dict | None:
    row = db.connect().execute(
        "SELECT id, username, display_name, email, role, is_active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def count_active_admins(excluding: int | None = None) -> int:
    conn = db.connect()
    if excluding is None:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()["n"]
    return conn.execute(
        """SELECT COUNT(*) AS n FROM users
           WHERE role = 'admin' AND is_active = 1 AND id != ?""",
        (excluding,),
    ).fetchone()["n"]


def set_role(user_id: int, role: Role | str) -> None:
    role = Role(role) if not isinstance(role, Role) else role
    if role is Role.ANON:
        raise ValueError("Cannot assign the anonymous role.")

    user = get_user(user_id)
    if user is None:
        raise ValueError("No such account.")

    # Demoting the last admin would leave nobody able to manage accounts, with
    # no way back short of editing the database by hand.
    if (user["role"] == Role.ADMIN.value and role is not Role.ADMIN
            and count_active_admins(excluding=user_id) == 0):
        raise ValueError("That is the only admin account. Promote another first.")

    conn = db.connect()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role.value, user_id))
    conn.commit()


def set_active(user_id: int, active: bool) -> None:
    user = get_user(user_id)
    if user is None:
        raise ValueError("No such account.")

    if not active:
        if user["role"] == Role.ADMIN.value and count_active_admins(excluding=user_id) == 0:
            raise ValueError("That is the only admin account.")
        destroy_sessions_for(user_id)

    conn = db.connect()
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
    conn.commit()


def delete_user(user_id: int) -> bool:
    user = get_user(user_id)
    if user is None:
        return False
    if user["role"] == Role.ADMIN.value and count_active_admins(excluding=user_id) == 0:
        raise ValueError("That is the only admin account.")

    conn = db.connect()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return True


def ensure_bypass_account() -> int:
    """The account the beta bypass signs in as, created on demand.

    A real row with a real session, so the bypass shares the ordinary
    authentication path instead of being a special case threaded through it.
    The password is random and never shown: the only way in is the bypass
    button, which the run mode can refuse.
    """
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (BYPASS_USERNAME,)
    ).fetchone()
    if row is not None:
        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return row["id"]

    return create_user(
        BYPASS_USERNAME,
        secrets.token_urlsafe(32),
        Role.GM,
        display_name="GM (beta bypass)",
    )


def is_bypass_principal(principal: Principal) -> bool:
    return principal.username == BYPASS_USERNAME


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #

CSRF_COOKIE = "ezvtt_csrf"
CSRF_FIELD = "csrf_token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_valid(cookie_value: str | None, form_value: str | None) -> bool:
    """Double-submit check.

    Session cookies are sent automatically on cross-site form posts, so without
    this a page on another origin could make a logged-in GM's browser submit
    account changes. An attacker can make the browser send the cookie but cannot
    read it to copy into the form field.
    """
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)
