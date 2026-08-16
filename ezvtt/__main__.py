"""Command line entry point: ``python -m ezvtt``.

Parses arguments, writes the PID file the stop/kill scripts read, prints the
banner a GM actually needs (which URL to give players), and runs uvicorn.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import logging
import os
import sys
import textwrap
import threading
import webbrowser

from . import __version__, config, net
from .config import DEFAULT_PORT, RunMode, Settings

log = logging.getLogger("ezvtt")


# --------------------------------------------------------------------------- #
# PID file
# --------------------------------------------------------------------------- #

def _write_pid_file() -> None:
    """Record our PID so stop/kill can find us from any shell.

    A stale file from a crashed run is overwritten rather than treated as a
    conflict: refusing to start because of a leftover file would be a worse
    failure than the one it guards against.
    """
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_remove_pid_file)


def _remove_pid_file() -> None:
    # A PID file we cannot delete is not worth failing shutdown over; the start
    # script clears a stale one anyway.
    with contextlib.suppress(OSError):
        config.PID_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Address discovery
# --------------------------------------------------------------------------- #

def _wrap(text: str, width: int = 68) -> list[str]:
    """Wrap for the console, preserving deliberate paragraph breaks."""
    if not text:
        return []
    out: list[str] = []
    for paragraph in text.split("\n"):
        out.extend(textwrap.wrap(paragraph, width=width) or [""])
    return out


def _banner(settings: Settings, hotspot: net.HotspotResult | None = None) -> str:
    policy = settings.policy
    lines = [
        "",
        f"  EzVTT {__version__}",
        f"  {policy.summary}",
        "",
    ]

    host_for_url = "localhost" if settings.host in ("0.0.0.0", "::") else settings.host
    lines.append(f"  GM screen       http://{host_for_url}:{settings.port}/")
    lines.append(f"  Display window  http://{host_for_url}:{settings.port}/display")

    if hotspot is not None:
        lines.append("")
        if hotspot.started:
            lines.append(f"  Wi-Fi network   {hotspot.ssid}")
        else:
            lines.append(f"  ! Hotspot not started: {hotspot.message}")
            lines += [f"  ! {line}" for line in _wrap(hotspot.detail)]
            lines.append("  ! Carrying on over the existing network instead.")

    if policy.show_join_qr or settings.mode is RunMode.INTERNET:
        # Only internet mode reaches out for a public address; every other mode
        # has to work with no route to the internet at all.
        public = net.public_address() if settings.mode is RunMode.INTERNET else None
        info = net.join_info(settings, public=public)

        if info.url:
            lines.append("")
            lines.append(f"  Players join at {info.url}")
            for alternative in info.alternatives:
                lines.append(f"          or      {alternative}")
            lines.append("")
            lines.append("  A QR code for that address is on the GM screen.")
        elif info.note:
            lines.append("")
            lines += [f"  {line}" for line in _wrap(info.note)]

    if settings.mode is RunMode.INTERNET:
        lines += [
            "",
            "  ! Reachable from the public internet.",
            f"  ! Forward port {settings.port} on your router to this machine.",
            "  ! Traffic is unencrypted HTTP unless you put TLS in front of it.",
            "  ! Authentication is forced on; the beta bypass is disabled.",
        ]

    if settings.mode is RunMode.VPS:
        lines += [
            "",
            "  ! Forwarded headers are trusted. Run this behind a reverse proxy",
            "  ! you control -- never expose it directly.",
        ]

    if settings.beta_bypass_available:
        lines += [
            "",
            "  * Beta login bypass is ON -- anyone who can reach this server can",
            "  * sign in as GM with one click. Disable with --no-bypass.",
        ]

    lines += ["", "  Ctrl+C to stop.", ""]
    return "\n".join(lines)


def _open_browser_when_ready(url: str, delay: float = 1.0) -> None:
    """Open the GM screen once the server has had a moment to bind."""
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezvtt",
        description="EzVTT -- a virtual tabletop built for getting a map on the "
                    "table fast.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "run modes:\n"
            "  local     this machine only (default)\n"
            "  lan       your local network; players join by QR code\n"
            "  hotspot   host a Wi-Fi access point -- best-effort, see docs\n"
            "  internet  public internet; auth forced on, bypass disabled\n"
            "  vps       behind a TLS-terminating reverse proxy\n"
            "\nSee docs/RUN_MODES.md for the details, especially for hotspot mode.\n"
        ),
    )
    parser.add_argument(
        "--mode", type=RunMode, choices=list(RunMode), default=RunMode.LOCAL,
        help="how EzVTT is reachable (default: local)",
    )
    parser.add_argument(
        "--host", default=None,
        help="address to bind (default: chosen by the run mode)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("EZVTT_PORT", DEFAULT_PORT)),
        help=f"port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="restart on source changes (development)",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="do not open the GM screen automatically",
    )

    bypass = parser.add_mutually_exclusive_group()
    bypass.add_argument(
        "--no-bypass", dest="bypass", action="store_false", default=None,
        help="disable the beta login bypass",
    )
    bypass.add_argument(
        "--bypass", dest="bypass", action="store_true", default=None,
        help="enable the beta login bypass (ignored in internet and vps modes)",
    )

    parser.add_argument(
        "--hotspot-ssid", default="EzVTT",
        help="network name to broadcast in hotspot mode (default: EzVTT)",
    )
    parser.add_argument(
        "--hotspot-password", default="",
        help="hotspot password, at least 8 characters (Windows requires one)",
    )

    parser.add_argument("--version", action="version", version=f"EzVTT {__version__}")

    # British spelling in what a user reads, with the American form accepted
    # silently -- half the world will type the other one.
    parser.add_argument(
        "--licences", "--licenses", dest="licences", action="store_true",
        help="print the licences of EzVTT and everything it ships, then exit",
    )
    return parser


def _print_licences() -> int:
    """Print the licence texts carried in this build.

    A packaged EzVTT redistributes its dependencies, which obliges it to carry
    their licence texts. Someone holding only the executable has nowhere else
    to look, so it can show them itself. See ADR-008 and ADR-016.
    """
    printed = False
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"):
        path = config.bundled_file(name)
        if path is None:
            continue
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}\n")
        print(path.read_text(encoding="utf-8").rstrip())
        printed = True

    if not printed:
        print(
            "No licence files found in this build. Source and licences: "
            "https://github.com/DavidKendig/EzVTT",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.licences:
        return _print_licences()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = Settings(
        mode=args.mode,
        host=args.host,
        port=args.port,
        reload=args.reload,
        bypass_override=args.bypass,
    )

    if args.bypass and not settings.policy.allow_beta_bypass:
        log.warning(
            "--bypass ignored: the login bypass is not permitted in %s mode.",
            settings.mode.value,
        )

    try:
        import uvicorn
    except ImportError:
        print(
            "Dependencies are not installed. Run the setup script first:\n"
            "  Windows        .\\scripts\\setup.ps1\n"
            "  macOS / Linux  ./scripts/setup.sh",
            file=sys.stderr,
        )
        return 1

    # Refuse to start over something already answering on this port. Binding
    # would otherwise *succeed*: a stale instance on 127.0.0.1 and a new one on
    # 0.0.0.0 coexist on Windows, loopback traffic goes to the stale one, and
    # the GM sees an old build with nothing anywhere to explain why.
    if net.port_already_serving(settings.port):
        print(
            f"\n  Something is already answering on port {settings.port}.\n"
            f"\n  If that is an older EzVTT, stop it first:\n"
            f"      .\\scripts\\stop.ps1          (or ./scripts/stop.sh)\n"
            f"      .\\scripts\\kill.ps1 -Port {settings.port}\n"
            f"\n  Otherwise choose another port with --port.\n",
            file=sys.stderr,
        )
        return 1

    # Best-effort, and genuinely likely to fail -- see docs/RUN_MODES.md. When it
    # does, the server still starts: an access point is a convenience, and a GM
    # on an existing network is better served by carrying on than by an abort.
    hotspot = None
    if settings.mode is RunMode.HOTSPOT:
        log.info("Attempting to start a Wi-Fi hotspot...")
        hotspot = net.start_hotspot(args.hotspot_ssid, args.hotspot_password)
        if hotspot.started:
            atexit.register(net.stop_hotspot)
        else:
            log.warning("Hotspot not started: %s", hotspot.message)

    _write_pid_file()

    # flush=True matters: Python block-buffers stdout when it is not a terminal,
    # so piping or redirecting the server would swallow this banner until the
    # buffer filled. In lan mode the banner carries the join URL players need --
    # it has to appear immediately, not eventually.
    print(_banner(settings, hotspot), flush=True)

    if settings.policy.open_browser and not args.no_browser and not args.reload:
        host = "localhost" if settings.host in ("0.0.0.0", "::") else settings.host
        _open_browser_when_ready(f"http://{host}:{settings.port}/")

    # --reload re-imports the app in a child process that never sees these
    # parsed arguments, so pass the settings through the environment.
    os.environ.update(settings.to_env())

    try:
        if args.reload:
            uvicorn.run(
                "ezvtt.app:create_app",
                factory=True,
                host=settings.host,
                port=settings.port,
                reload=True,
                reload_dirs=[str(config.PACKAGE_ROOT)],
                log_level="info",
            )
        else:
            from .app import create_app

            uvicorn.run(
                create_app(settings),
                host=settings.host,
                port=settings.port,
                log_level="info",
            )
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    except OSError as exc:
        # Almost always "address already in use" -- worth naming, because the
        # fix is a different port or stopping the other instance.
        print(f"\n  Could not start on {settings.bind_description}: {exc}\n",
              file=sys.stderr)
        return 1
    finally:
        _remove_pid_file()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
