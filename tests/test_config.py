"""Run-mode policy.

These are security assertions, not preferences. The beta bypass signs a caller
in as GM with one unauthenticated click; a mode that faces the public internet
must never offer it, and no flag may talk it into doing so.
"""

import pytest

from ezvtt.config import RunMode, Settings, policy_for


def test_local_binds_loopback_only():
    assert Settings(mode=RunMode.LOCAL).host == "127.0.0.1"


@pytest.mark.parametrize(
    "mode", [RunMode.LAN, RunMode.HOTSPOT, RunMode.INTERNET, RunMode.VPS]
)
def test_networked_modes_bind_all_interfaces(mode):
    assert Settings(mode=mode).host == "0.0.0.0"


@pytest.mark.parametrize("mode", [RunMode.INTERNET, RunMode.VPS])
def test_public_modes_forbid_the_bypass(mode):
    assert Settings(mode=mode).beta_bypass_available is False
    assert policy_for(mode).require_auth is True


@pytest.mark.parametrize("mode", [RunMode.INTERNET, RunMode.VPS])
def test_explicit_bypass_flag_cannot_override_a_public_mode(mode):
    """--bypass is a convenience, not an escape hatch."""
    assert Settings(mode=mode, bypass_override=True).beta_bypass_available is False


@pytest.mark.parametrize("mode", [RunMode.LOCAL, RunMode.LAN, RunMode.HOTSPOT])
def test_private_modes_allow_the_bypass_by_default(mode):
    assert Settings(mode=mode).beta_bypass_available is True


@pytest.mark.parametrize("mode", [RunMode.LOCAL, RunMode.LAN, RunMode.HOTSPOT])
def test_no_bypass_flag_is_honoured_where_the_bypass_is_permitted(mode):
    assert Settings(mode=mode, bypass_override=False).beta_bypass_available is False


def test_only_vps_trusts_forwarded_headers():
    """Trusting X-Forwarded-* without a proxy in front lets clients forge them."""
    trusting = [m for m in RunMode if policy_for(m).trust_proxy_headers]
    assert trusting == [RunMode.VPS]


def test_only_vps_marks_cookies_secure():
    # The other modes routinely run over plain HTTP; Secure cookies there would
    # simply never be sent, silently breaking login.
    secure = [m for m in RunMode if policy_for(m).secure_cookies]
    assert secure == [RunMode.VPS]


def test_settings_round_trip_through_environment():
    """--reload re-imports the app in a child process that only sees env vars."""
    original = Settings(mode=RunMode.LAN, port=9999, bypass_override=False)
    env = original.to_env()

    import os

    saved = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        restored = Settings.from_env()
        assert restored.mode == original.mode
        assert restored.port == original.port
        assert restored.beta_bypass_available == original.beta_bypass_available
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
