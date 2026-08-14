"""Runtime configuration: filesystem paths and per-run-mode policy.

Two things live here. First, where things are on disk -- resolved once, at import
time, and tolerant of running from a PyInstaller bundle where code and data are
in different places. Second, what each run mode implies for network exposure and
security, which is policy rather than preference and is therefore expressed as
code rather than left to documentation.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

APP_NAME = "EzVTT"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def _package_root() -> Path:
    """Directory containing this package's code and bundled static assets."""
    return Path(__file__).resolve().parent


def _project_root() -> Path:
    """Directory holding mutable state: the database, uploads, the PID file.

    Running from source this is the repository root. Running from a PyInstaller
    one-file build, ``__file__`` points inside a temporary extraction directory
    that is deleted on exit -- writing a campaign database there would silently
    lose it. Fall back to the directory containing the executable instead.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    override = os.environ.get("EZVTT_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    return _package_root().parent


PACKAGE_ROOT = _package_root()
PROJECT_ROOT = _project_root()

TEMPLATES_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"
MIGRATIONS_DIR = PACKAGE_ROOT / "migrations"

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "ezvtt.db"
MAPS_DIR = DATA_DIR / "maps"
UPLOADS_DIR = DATA_DIR / "assets"
THUMBS_DIR = DATA_DIR / "thumbs"
# Player-visible map composites: the battlemap with unrevealed cells painted
# out. Players are served these and never the originals in MAPS_DIR, which is
# what makes fog of war an access-control boundary rather than an overlay.
FOG_DIR = DATA_DIR / "fog"
RUN_DIR = DATA_DIR / "run"
PID_FILE = RUN_DIR / "ezvtt.pid"

# Artwork installed by scripts/fetch-assets. Read-only at runtime: Tom's Open Map
# License does not permit editing these files, so nothing may write here.
# See ADR-005 in docs/DECISIONS.md.
BUNDLED_ASSETS_DIR = PROJECT_ROOT / "assets" / "bundled"

WRITABLE_DIRS = (DATA_DIR, MAPS_DIR, UPLOADS_DIR, THUMBS_DIR, FOG_DIR, RUN_DIR)


def ensure_directories() -> None:
    """Create the runtime directories. Safe to call repeatedly."""
    for directory in WRITABLE_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Run modes
# --------------------------------------------------------------------------- #

class RunMode(str, Enum):
    LOCAL = "local"
    LAN = "lan"
    HOTSPOT = "hotspot"
    INTERNET = "internet"
    VPS = "vps"


@dataclass(frozen=True)
class ModePolicy:
    """What a run mode implies. Security posture, not user preference."""

    default_host: str
    allow_beta_bypass: bool
    require_auth: bool
    trust_proxy_headers: bool
    secure_cookies: bool
    open_browser: bool
    show_join_qr: bool
    summary: str


# Exposure to the public internet forces authentication on and the beta bypass
# off. This is deliberately not configurable: the bypass signs the caller in as
# GM with one unauthenticated click, which is a reasonable convenience on
# loopback and an open door anywhere else.
_POLICIES: dict[RunMode, ModePolicy] = {
    RunMode.LOCAL: ModePolicy(
        default_host="127.0.0.1",
        allow_beta_bypass=True,
        require_auth=False,
        trust_proxy_headers=False,
        secure_cookies=False,
        open_browser=True,
        show_join_qr=False,
        summary="This machine only. Nothing is reachable from the network.",
    ),
    RunMode.LAN: ModePolicy(
        default_host="0.0.0.0",
        allow_beta_bypass=True,
        require_auth=False,
        trust_proxy_headers=False,
        secure_cookies=False,
        open_browser=True,
        show_join_qr=True,
        summary="Reachable from your local network. Players can join by QR code.",
    ),
    RunMode.HOTSPOT: ModePolicy(
        default_host="0.0.0.0",
        allow_beta_bypass=True,
        require_auth=False,
        trust_proxy_headers=False,
        secure_cookies=False,
        open_browser=True,
        show_join_qr=True,
        summary="Hosting a Wi-Fi access point. Best-effort; falls back to LAN.",
    ),
    RunMode.INTERNET: ModePolicy(
        default_host="0.0.0.0",
        allow_beta_bypass=False,
        require_auth=True,
        trust_proxy_headers=False,
        secure_cookies=False,
        open_browser=False,
        show_join_qr=False,
        summary="Reachable from the public internet. Auth forced on, bypass disabled.",
    ),
    RunMode.VPS: ModePolicy(
        default_host="0.0.0.0",
        allow_beta_bypass=False,
        require_auth=True,
        # Only safe because this mode is documented as proxy-only. A directly
        # exposed server must never trust forwarded headers -- a client can
        # forge them to spoof its source address.
        trust_proxy_headers=True,
        secure_cookies=True,
        open_browser=False,
        show_join_qr=False,
        summary="Behind a TLS-terminating reverse proxy. Auth forced on.",
    ),
}


def policy_for(mode: RunMode) -> ModePolicy:
    return _POLICIES[mode]


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

DEFAULT_PORT = 8080


@dataclass
class Settings:
    """Resolved configuration for one run of the server."""

    mode: RunMode = RunMode.LOCAL
    host: str | None = None
    port: int = DEFAULT_PORT
    reload: bool = False
    bypass_override: bool | None = None
    policy: ModePolicy = field(init=False)

    def __post_init__(self) -> None:
        self.policy = policy_for(self.mode)
        if self.host is None:
            self.host = self.policy.default_host

    @property
    def beta_bypass_available(self) -> bool:
        """Whether the login screen may offer the bypass button at all.

        A mode that forbids the bypass wins outright; ``--bypass`` cannot turn it
        back on. Within a permitted mode, ``--no-bypass`` can still switch it off.
        """
        if not self.policy.allow_beta_bypass:
            return False
        if self.bypass_override is not None:
            return self.bypass_override
        return True

    @property
    def bind_description(self) -> str:
        if self.host in ("0.0.0.0", "::"):
            return f"all interfaces, port {self.port}"
        return f"{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from EZVTT_* environment variables.

        Used by the reload path, where uvicorn re-imports the application in a
        fresh process that never sees the parsed command line.
        """
        mode = RunMode(os.environ.get("EZVTT_MODE", RunMode.LOCAL.value))
        bypass_raw = os.environ.get("EZVTT_BYPASS")
        return cls(
            mode=mode,
            host=os.environ.get("EZVTT_HOST") or None,
            port=int(os.environ.get("EZVTT_PORT", DEFAULT_PORT)),
            bypass_override=(
                None if bypass_raw is None else bypass_raw.lower() in ("1", "true", "yes")
            ),
        )

    def to_env(self) -> dict[str, str]:
        env = {
            "EZVTT_MODE": self.mode.value,
            "EZVTT_HOST": self.host or "",
            "EZVTT_PORT": str(self.port),
        }
        if self.bypass_override is not None:
            env["EZVTT_BYPASS"] = "1" if self.bypass_override else "0"
        return env
