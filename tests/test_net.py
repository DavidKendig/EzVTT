"""Addresses, join URLs, and QR rendering.

The QR tests reconstruct the module matrix back out of the generated SVG and
compare it to what the encoder produced. The encoder is a well-tested library;
the run-length SVG packing is mine, and that is the part that can be wrong.
"""

import ipaddress
import re

import pytest

from ezvtt import net
from ezvtt.config import RunMode, Settings

# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #

def test_lan_address_is_a_real_non_loopback_address():
    address = net.lan_address()
    if address is None:
        pytest.skip("no network on this machine")
    parsed = ipaddress.ip_address(address)
    assert not parsed.is_loopback, "127.0.0.1 is useless for players to type"


def test_all_addresses_include_the_primary_first():
    addresses = net.all_lan_addresses()
    primary = net.lan_address()
    if primary is None:
        pytest.skip("no network on this machine")
    assert addresses[0] == primary
    assert len(addresses) == len(set(addresses)), "duplicates would confuse the GM"


def test_no_loopback_or_link_local_in_the_list():
    for address in net.all_lan_addresses():
        parsed = ipaddress.ip_address(address)
        assert not parsed.is_loopback
        assert not parsed.is_link_local


# --------------------------------------------------------------------------- #
# Join URLs
# --------------------------------------------------------------------------- #

def test_local_mode_has_nothing_to_share():
    """Printing an address in local mode would be a lie -- nobody can reach it."""
    info = net.join_info(Settings(mode=RunMode.LOCAL, port=8080))
    assert info.shareable is False
    assert info.url is None
    assert "lan mode" in info.note


def test_vps_mode_does_not_guess_an_address():
    """Behind a proxy the public name is the proxy's, which we cannot know."""
    info = net.join_info(Settings(mode=RunMode.VPS, port=8080))
    assert info.url is None
    assert "domain" in info.note


@pytest.mark.parametrize("mode", [RunMode.LAN, RunMode.HOTSPOT])
def test_lan_and_hotspot_offer_a_join_url(mode):
    if not net.all_lan_addresses():
        pytest.skip("no network on this machine")
    info = net.join_info(Settings(mode=mode, port=8080))
    assert info.shareable
    assert info.url.endswith(":8080/play")
    assert info.url.startswith("http://")


def test_internet_mode_uses_the_public_address_when_known():
    info = net.join_info(Settings(mode=RunMode.INTERNET, port=9000),
                         public="203.0.113.7")
    assert info.url == "http://203.0.113.7:9000/play"
    assert "Forward port 9000" in info.note


def test_internet_mode_says_so_when_the_public_address_is_unknown():
    info = net.join_info(Settings(mode=RunMode.INTERNET, port=9000), public=None)
    assert info.url is None
    assert "9000" in info.note


def test_the_join_url_points_at_the_player_view():
    """Not the GM screen -- handing players that URL is the classic mistake."""
    if not net.all_lan_addresses():
        pytest.skip("no network on this machine")
    assert net.join_info(Settings(mode=RunMode.LAN)).url.endswith("/play")


# --------------------------------------------------------------------------- #
# QR codes
# --------------------------------------------------------------------------- #

def _matrix_from_svg(svg: str) -> list[list[bool]]:
    """Rebuild the module matrix from the generated SVG."""
    size = int(re.search(r'viewBox="0 0 (\d+) ', svg).group(1))
    matrix = [[False] * size for _ in range(size)]

    # Skip the first rect: it is the white background, not a module.
    rects = re.findall(
        r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"/>', svg
    )
    for x, y, width, _height in rects:
        for column in range(int(x), int(x) + int(width)):
            matrix[int(y)][column] = True
    return matrix


def test_svg_reproduces_the_encoder_matrix_exactly():
    """The run-length packing must not drop or shift a single module."""
    qrcode = pytest.importorskip("qrcode")

    url = "http://192.168.1.13:8080/play"
    code = qrcode.QRCode(border=2, box_size=1)
    code.add_data(url)
    code.make(fit=True)
    expected = code.get_matrix()

    svg = net.qr_svg(url, quiet_zone=2)
    assert svg is not None
    assert _matrix_from_svg(svg) == [[bool(c) for c in row] for row in expected]


def test_run_length_packing_actually_saves_elements():
    """One rect per module would make the inlined markup several times larger."""
    svg = net.qr_svg("http://192.168.1.13:8080/play")
    rects = svg.count("<rect")
    dark = sum(row.count(True) for row in _matrix_from_svg(svg))
    assert rects < dark, f"{rects} rects for {dark} dark modules"


def test_qr_is_self_contained_and_inlineable():
    svg = net.qr_svg("http://example.test:8080/play")
    assert svg.startswith("<svg")
    assert "xmlns=" in svg
    # No external reference of any kind: EzVTT must render with no network.
    assert "http://www.w3.org/2000/svg" in svg
    assert ".css" not in svg and "<image" not in svg and "<script" not in svg


def test_qr_label_escapes_the_url():
    """The URL lands in an aria-label attribute."""
    svg = net.qr_svg('http://x/"><script>alert(1)</script>')
    assert "<script>" not in svg
    assert "&quot;" in svg or "&lt;" in svg


@pytest.mark.parametrize("text", [
    "http://192.168.1.13:8080/play",
    "http://10.0.0.1:9/play",
    "http://203.0.113.255:65535/play",
])
def test_qr_encodes_realistic_join_urls(text):
    svg = net.qr_svg(text)
    assert svg is not None
    size = int(re.search(r'viewBox="0 0 (\d+) ', svg).group(1))
    assert size >= 21, "smaller than the minimum QR version"


# --------------------------------------------------------------------------- #
# Port collision
# --------------------------------------------------------------------------- #

def test_port_already_serving_detects_a_listener():
    """Guards the failure where a stale instance silently shadows a new one."""
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert net.port_already_serving(port) is True
    finally:
        listener.close()

    # Closed again, so nothing should answer.
    assert net.port_already_serving(port) is False


# --------------------------------------------------------------------------- #
# Hotspot
# --------------------------------------------------------------------------- #

def test_hotspot_failure_is_actionable_not_just_false():
    """A GM who cannot start an AP needs to know what to do instead."""
    result = net.start_hotspot("EzVTT-Test", "a-long-enough-password")
    assert isinstance(result.started, bool)
    if not result.started:
        assert result.message, "a failure with no message is unhelpable"
        # Either the message or the detail must suggest a way forward.
        combined = f"{result.message} {result.detail}".lower()
        assert any(word in combined for word in
                   ("mobile hotspot", "lan mode", "networkmanager", "nmcli",
                    "system settings", "not supported", "password"))
