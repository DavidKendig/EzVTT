"""Addresses, join URLs, QR codes, and best-effort Wi-Fi hotspots.

The job of this module is to answer one question for the GM: *what do I tell my
players to type?* Getting that wrong is the difference between a game starting
and ten minutes of everyone reading IP addresses aloud.

Nothing here reaches the internet unless the run mode already implies it. The
LAN address is derived locally, and the public-address lookup is only attempted
in ``internet`` mode, where the GM has by definition already chosen to be
reachable from outside.
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .config import RunMode, Settings

log = logging.getLogger("ezvtt.net")

# Anything slower than this is not worth making the GM wait for; they can read
# their own address off the router.
PUBLIC_IP_TIMEOUT_SECONDS = 3.0
HOTSPOT_TIMEOUT_SECONDS = 20


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #

def lan_address() -> str | None:
    """Best guess at this machine's address on the local network.

    Opens a UDP socket toward a public address and reads back the local end. No
    packet is sent -- a UDP connect only fixes the socket's peer -- but it makes
    the routing table choose the interface that would actually carry traffic,
    which beats guessing from a list of interfaces on a machine with a VPN, a
    virtual switch, and three adapters.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    # A loopback answer means there is no usable network, not an address worth
    # printing for players to type.
    return None if parsed.is_loopback else address


def all_lan_addresses() -> list[str]:
    """Every private IPv4 address this machine holds, best first.

    A GM on a laptop with Wi-Fi, Ethernet, and a VM switch has several. The
    routing-table guess above is usually right, but showing the alternatives
    saves a support conversation when it is not.
    """
    primary = lan_address()
    found: list[str] = [primary] if primary else []

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local:
                continue
            if address not in found:
                found.append(address)
    except OSError:
        pass

    return found


def port_already_serving(port: int, timeout: float = 0.4) -> bool:
    """Whether something is already answering on loopback at ``port``.

    Worth checking before binding, because on Windows a server on
    ``127.0.0.1:PORT`` and another on ``0.0.0.0:PORT`` can coexist happily --
    neither reports "address already in use". Loopback requests then go to the
    more specific binding, so a stale instance silently shadows the new one and
    the GM sees an old build with no error anywhere to explain it.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def public_address(timeout: float = PUBLIC_IP_TIMEOUT_SECONDS) -> str | None:
    """This machine's address as the internet sees it, or None.

    **Makes an outbound request**, so it is only called in ``internet`` mode --
    where being reachable from outside is the whole point. It is never called in
    local, lan, or hotspot mode, all of which must work with no internet at all.

    Fails silently: a GM without a route out still gets a working LAN address.
    """
    import urllib.error
    import urllib.request

    # Plain-text endpoints that return nothing but an address. Tried in order;
    # the first that answers wins.
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip",
                "https://icanhazip.com"):
        try:
            # S310 wants the scheme audited. These three are literals in this
            # file, all https, and no caller supplies a URL -- there is no path
            # by which a file: or custom scheme reaches here.
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                text = response.read(64).decode("ascii", "ignore").strip()
            ipaddress.ip_address(text)   # rejects an error page pretending to be an IP
            return text
        except (urllib.error.URLError, ValueError, OSError, UnicodeDecodeError):
            continue

    log.info("Could not determine the public address; continuing without it.")
    return None


# --------------------------------------------------------------------------- #
# Join URLs
# --------------------------------------------------------------------------- #

@dataclass
class JoinInfo:
    """What to tell players, and whether it is worth showing at all."""

    url: str | None
    host: str | None
    port: int
    mode: str
    alternatives: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def shareable(self) -> bool:
        """False in local mode, where nobody else can reach this machine."""
        return self.url is not None


def join_info(settings: Settings, public: str | None = None) -> JoinInfo:
    """The address players should open, for this run mode."""
    port = settings.port
    mode = settings.mode

    if mode is RunMode.LOCAL:
        return JoinInfo(
            url=None, host=None, port=port, mode=mode.value,
            note="Local mode is this machine only. Start in lan mode to let "
                 "players join from their own devices.",
        )

    if mode is RunMode.VPS:
        # Behind a proxy the public name is the proxy's, which the server has no
        # reliable way to know. Say so rather than printing a useless address.
        return JoinInfo(
            url=None, host=None, port=port, mode=mode.value,
            note="Players join at your domain name, through the reverse proxy "
                 "in front of EzVTT.",
        )

    if mode is RunMode.INTERNET:
        if public:
            return JoinInfo(
                url=f"http://{public}:{port}/play", host=public, port=port,
                mode=mode.value,
                alternatives=[f"http://{a}:{port}/play" for a in all_lan_addresses()],
                note=f"Forward port {port} on your router to this machine, or "
                     f"players will not get through.",
            )
        return JoinInfo(
            url=None, host=None, port=port, mode=mode.value,
            note=f"Could not determine this machine's public address. Find it "
                 f"at whatismyip.com and share http://<that address>:{port}/play.",
        )

    # lan and hotspot
    addresses = all_lan_addresses()
    if not addresses:
        return JoinInfo(
            url=None, host=None, port=port, mode=mode.value,
            note="No network address found. Check that Wi-Fi or Ethernet is "
                 "connected.",
        )

    primary, *rest = addresses
    return JoinInfo(
        url=f"http://{primary}:{port}/play", host=primary, port=port,
        mode=mode.value,
        alternatives=[f"http://{a}:{port}/play" for a in rest],
        note="If this address does not work, try one of the alternatives -- a "
             "machine with a VPN or a virtual switch has several."
             if rest else "",
    )


# --------------------------------------------------------------------------- #
# QR codes
# --------------------------------------------------------------------------- #

def qr_svg(text: str, quiet_zone: int = 2, size_px: int = 240) -> str | None:
    """Render ``text`` as a self-contained SVG QR code, or None if unavailable.

    Built from the raw module matrix rather than one of ``qrcode``'s image
    factories: those pull in Pillow or lxml backends and produce either a raster
    or markup we cannot style. A handful of ``<rect>`` elements scales cleanly
    to any screen, costs no extra dependency, and can be inlined straight into
    the page so it works with no network at all.
    """
    try:
        import qrcode
    except ImportError:
        log.info("qrcode is not installed; join QR codes are unavailable.")
        return None

    try:
        code = qrcode.QRCode(border=quiet_zone, box_size=1)
        code.add_data(text)
        code.make(fit=True)
        matrix = code.get_matrix()
    except Exception:
        log.warning("Could not encode %r as a QR code.", text, exc_info=True)
        return None

    modules = len(matrix)
    if not modules:
        return None

    # One rect per run of dark modules in a row rather than one per module:
    # a join URL is around 45x45 modules, so this turns ~1000 elements into a
    # couple of hundred and keeps the inlined markup small.
    rects: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < modules:
            if not row[x]:
                x += 1
                continue
            run = x
            while run < modules and row[run]:
                run += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{run - x}" height="1"/>')
            x = run

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {modules} {modules}" '
        f'width="{size_px}" height="{size_px}" shape-rendering="crispEdges" '
        f'role="img" aria-label="QR code for {escape(text, {chr(34): "&quot;"})}">'
        f'<rect width="{modules}" height="{modules}" fill="#ffffff"/>'
        f'<g fill="#000000">{"".join(rects)}</g>'
        f"</svg>"
    )


# --------------------------------------------------------------------------- #
# Hotspot
# --------------------------------------------------------------------------- #

@dataclass
class HotspotResult:
    started: bool
    message: str
    ssid: str = ""
    detail: str = ""


def _run(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            command, capture_output=True, text=True,
            timeout=HOTSPOT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def start_hotspot(ssid: str = "EzVTT", password: str = "") -> HotspotResult:
    """Best-effort host access point.

    This fails often, and not because of EzVTT -- creating a software AP has
    become steadily harder on every platform. The contract is therefore to
    report *why* it failed in terms a GM can act on, and let the caller fall
    back to ``lan``. See docs/RUN_MODES.md.
    """
    system = platform.system()

    if system == "Windows":
        return _start_hotspot_windows(ssid, password)
    if system == "Linux":
        return _start_hotspot_linux(ssid, password)
    if system == "Darwin":
        return HotspotResult(
            started=False,
            message="macOS cannot start a hotspot from the command line.",
            detail="Internet Sharing has no scriptable interface, and macOS "
                   "will not share a Wi-Fi connection over Wi-Fi. Enable it by "
                   "hand in System Settings, or use lan mode on an existing "
                   "network.",
        )

    return HotspotResult(
        started=False,
        message=f"Hotspot mode is not supported on {system}.",
    )


def _start_hotspot_windows(ssid: str, password: str) -> HotspotResult:
    """Try the legacy hosted-network interface, and explain when it is absent.

    The modern replacement is Mobile Hotspot, which is only reachable through a
    WinRT API and needs a packaged app identity to call. Rather than ship that
    machinery, EzVTT tries the old interface and tells the GM exactly which
    switch to flick when it is gone -- which on most current drivers it is.
    """
    manual = (
        "Turn on Settings → Network & Internet → Mobile hotspot, then "
        "start EzVTT again in lan mode -- it will pick up the hotspot's address "
        "automatically."
    )

    code, output = _run(["netsh", "wlan", "show", "drivers"])
    if code != 0:
        return HotspotResult(
            started=False,
            message="Could not query the Wi-Fi driver.",
            detail=f"{manual}\n\n{output[:300]}",
        )

    # The wording is localised, so match the value rather than the label.
    supported = "yes" in output.lower().split("hosted network supported")[-1][:12] \
        if "hosted network supported" in output.lower() else False
    if not supported:
        return HotspotResult(
            started=False,
            message="This Wi-Fi driver does not support the hosted-network "
                    "interface, which most current drivers have dropped.",
            detail=manual,
        )

    if len(password) < 8:
        return HotspotResult(
            started=False,
            message="A Windows hotspot needs a password of at least 8 characters.",
            detail="Pass one with --hotspot-password.",
        )

    code, output = _run([
        "netsh", "wlan", "set", "hostednetwork", "mode=allow",
        f"ssid={ssid}", f"key={password}",
    ])
    if code != 0:
        return HotspotResult(started=False,
                             message="Could not configure the hosted network.",
                             detail=f"{manual}\n\n{output[:300]}")

    code, output = _run(["netsh", "wlan", "start", "hostednetwork"])
    if code != 0:
        return HotspotResult(started=False,
                             message="Could not start the hosted network.",
                             detail=f"{manual}\n\n{output[:300]}")

    return HotspotResult(started=True, ssid=ssid,
                         message=f"Hosted network {ssid!r} started.")


def _start_hotspot_linux(ssid: str, password: str) -> HotspotResult:
    if shutil.which("nmcli") is None:
        return HotspotResult(
            started=False,
            message="nmcli was not found, so EzVTT cannot create a hotspot.",
            detail="Install NetworkManager, or create the access point by hand "
                   "and run EzVTT in lan mode.",
        )

    command = ["nmcli", "device", "wifi", "hotspot", "ssid", ssid]
    if password:
        command += ["password", password]

    code, output = _run(command)
    if code != 0:
        return HotspotResult(
            started=False,
            message="NetworkManager refused to start the hotspot.",
            detail="Many USB adapters cannot act as an access point. Check with "
                   "`iw list | grep -A 10 \"Supported interface modes\"` and "
                   "look for AP.\n\n" + output[:300],
        )

    return HotspotResult(started=True, ssid=ssid,
                         message=f"Hotspot {ssid!r} started via NetworkManager.")


def stop_hotspot() -> HotspotResult:
    system = platform.system()
    if system == "Windows":
        code, output = _run(["netsh", "wlan", "stop", "hostednetwork"])
    elif system == "Linux":
        code, output = _run(["nmcli", "connection", "down", "Hotspot"])
    else:
        return HotspotResult(started=False, message="Nothing to stop.")

    return HotspotResult(
        started=False,
        message="Hotspot stopped." if code == 0 else "Could not stop the hotspot.",
        detail=output[:300],
    )
