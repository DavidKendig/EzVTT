#!/usr/bin/env python3
"""Start a built EzVTT, prove it actually works, and stop it.

The point of this is everything PyInstaller can silently get wrong. A bundle
that imports cleanly can still be missing its templates, its migrations, or its
static files -- and none of that shows up until a GM opens the page. So this
starts the real binary, on a real port, against a scratch data directory, and
checks the things a broken bundle would fail:

* it answers /health at all                     (imports, event loop, bootloader)
* it reports first_run on an empty directory    (migrations ran, DB is writable)
* the database landed *outside* the bundle      (ADR-016: state next to the exe)
* / redirects to the setup wizard               (Jinja templates are present)
* /static/css/ezvtt.css is served               (static files are present)
* /setup renders HTML mentioning EzVTT          (a template actually rendered)
* --licences prints the dependency licences     (ADR-008 compliance in the bundle)

Usage::

    python scripts/smoke_test.py dist/ezvtt
    python scripts/smoke_test.py -- .venv/Scripts/python -m ezvtt

Exits non-zero, loudly, on the first failure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

STARTUP_TIMEOUT = 90.0        # a one-file bundle unpacks itself on first run
POLL_INTERVAL = 0.25


class SmokeFailure(Exception):
    """A check failed. The message is what a human needs to know."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(url: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Fetch a URL without following redirects. Returns status, body, location."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), exc.headers.get("Location", "")


def wait_for_health(url: str, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last_error = "no attempt made"

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeFailure(
                f"EzVTT exited with code {process.returncode} before it was ready."
            )
        try:
            status, body, _ = get(url, timeout=3.0)
            if status == 200:
                return json.loads(body)
            last_error = f"HTTP {status}"
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(POLL_INTERVAL)

    raise SmokeFailure(f"EzVTT never answered {url} ({last_error}).")


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}", flush=True)
        return
    raise SmokeFailure(f"{label}{': ' + detail if detail else ''}")


def stop(process: subprocess.Popen) -> None:
    """End the server and everything it started.

    A PyInstaller one-file binary is **two** processes: the bootloader, which
    unpacks the bundle, and the real program it then runs. Killing only the
    bootloader leaves a web server running and holding the executable open --
    on Windows the next build then fails with "access is denied" on a file
    nothing appears to be using, which is a genuinely puzzling half hour.

    The same two processes are why output goes to a file rather than a pipe:
    the survivor inherits the pipe and reading it to completion blocks for as
    long as it lives, which for a server is indefinitely.
    """
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()

    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run_checks(command: list[str], data_root: Path) -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    environment = dict(os.environ)
    environment["EZVTT_DATA_ROOT"] = str(data_root)

    print(f"  ->    {' '.join(command)} --mode local --port {port} --no-browser",
          flush=True)
    log = data_root / "ezvtt.log"
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(
            [*command, "--mode", "local", "--port", str(port), "--no-browser"],
            env=environment,
            stdout=sink,
            stderr=subprocess.STDOUT,
            text=True,
            # Its own process group, so stop() can take the bootloader and the
            # program it unpacked together. Windows does the same with /T.
            start_new_session=os.name != "nt",
        )

    try:
        health = wait_for_health(f"{base}/health", process)
        check("answers /health", health.get("status") == "ok", json.dumps(health))
        check("reports a version", bool(health.get("version")), json.dumps(health))
        check(
            "starts at first run on an empty data directory",
            health.get("first_run") is True,
            json.dumps(health),
        )

        database = data_root / "data" / "ezvtt.db"
        check(
            "writes its database outside the bundle",
            database.is_file(),
            f"expected {database}",
        )

        status, _, location = get(f"{base}/")
        check(
            "sends a new install to the setup wizard",
            status == 303 and location.endswith("/setup"),
            f"HTTP {status} -> {location or 'nowhere'}",
        )

        status, body, _ = get(f"{base}/setup")
        check("renders a template", status == 200 and "EzVTT" in body, f"HTTP {status}")

        status, body, _ = get(f"{base}/static/css/ezvtt.css")
        check(
            "serves its static files",
            status == 200 and "--surface-0" in body,
            f"HTTP {status}, {len(body)} bytes",
        )
    except SmokeFailure:
        # The server's own output is the only useful thing left to say.
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if tail:
            print("\n  --- EzVTT said ---", flush=True)
            for line in tail[-25:]:
                print(f"  {line}", flush=True)
            print("  ------------------\n", flush=True)
        raise
    finally:
        stop(process)
        if os.environ.get("EZVTT_SMOKE_VERBOSE"):
            print(log.read_text(encoding="utf-8", errors="replace"))


def check_licences(command: list[str]) -> None:
    """A bundled build must be able to show the licences it ships (ADR-008)."""
    result = subprocess.run(
        [*command, "--licences"], capture_output=True, text=True, timeout=120,
    )
    check("prints its licences", result.returncode == 0, result.stderr.strip())
    for expected in ("Apache License", "THIRD_PARTY_LICENSES"):
        check(
            f"licence output mentions {expected!r}",
            expected in result.stdout,
            f"{len(result.stdout)} characters printed",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="the EzVTT to test, e.g. dist/ezvtt or '-- python -m ezvtt'",
    )
    args = parser.parse_args(argv)

    command = [part for part in args.command if part != "--"]
    if not command:
        parser.error("give the command that starts EzVTT, e.g. dist/ezvtt")

    target = Path(command[0])
    if target.exists():
        command[0] = str(target.resolve())
    else:
        # shutil.which also tries the Windows PATHEXT extensions, which is how
        # ".venv/Scripts/python" finds python.exe. Use what it found rather
        # than the spelling that does not exist on disk.
        resolved = shutil.which(command[0])
        if resolved is None:
            print(f"Not found: {command[0]}", file=sys.stderr)
            return 2
        command[0] = resolved

    print(f"\n  EzVTT smoke test\n  {' '.join(command)}\n")

    data_root = Path(tempfile.mkdtemp(prefix="ezvtt-smoke-"))
    try:
        run_checks(command, data_root)
        check_licences(command)
    except SmokeFailure as failure:
        print(f"\n  FAILED: {failure}\n", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(data_root, ignore_errors=True)

    print("\n  All checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
