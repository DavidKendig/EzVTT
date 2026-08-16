"""Password hashing, session signing, and role ordering."""

import pytest

from ezvtt.auth import (
    ANONYMOUS,
    PasswordHash,
    Principal,
    Role,
    hash_password,
    verify_password,
)

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

def test_correct_password_verifies():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_wrong_password_is_rejected():
    stored = hash_password("correct horse battery staple")
    assert not verify_password("Correct horse battery staple", stored)
    assert not verify_password("", stored)


def test_same_password_hashes_differently_each_time():
    """A per-user salt is what stops one rainbow table covering every account."""
    a = hash_password("shared password")
    b = hash_password("shared password")
    assert a.salt_hex != b.salt_hex
    assert a.hash_hex != b.hash_hex
    # Both still verify against their own salt.
    assert verify_password("shared password", a)
    assert verify_password("shared password", b)


def test_verification_uses_the_cost_stored_with_the_hash():
    """Cost parameters can be raised later without invalidating old passwords."""
    stored = hash_password("secret")
    assert stored.n >= 2**14
    # A hash recorded with different cost must still verify on its own terms.
    cheap = PasswordHash(stored.hash_hex, stored.salt_hex, stored.n, stored.r, stored.p)
    assert verify_password("secret", cheap)


def test_malformed_stored_hash_is_rejected_not_crashed():
    """Corrupt data in the users table must fail closed, not raise."""
    assert not verify_password("anything", PasswordHash("zzzz", "not hex", 2**14, 8, 1))


def test_unicode_passwords_round_trip():
    stored = hash_password("pässwörd-ünïcode-🎲")
    assert verify_password("pässwörd-ünïcode-🎲", stored)
    assert not verify_password("passwörd-ünïcode-🎲", stored)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

def test_role_ordering():
    admin = Principal(1, "a", "A", Role.ADMIN)
    gm = Principal(2, "g", "G", Role.GM)
    player = Principal(3, "p", "P", Role.PLAYER)

    assert admin.has_at_least(Role.GM)
    assert gm.has_at_least(Role.GM)
    assert not player.has_at_least(Role.GM)
    assert player.has_at_least(Role.PLAYER)
    assert not ANONYMOUS.has_at_least(Role.PLAYER)


def test_admin_counts_as_gm():
    """The admin runs the server; locking them out of GM tools would be absurd."""
    assert Principal(1, "a", "A", Role.ADMIN).is_gm


def test_anonymous_is_not_authenticated():
    assert not ANONYMOUS.is_authenticated
    assert not ANONYMOUS.is_gm
    assert not ANONYMOUS.is_admin


def test_via_bypass_defaults_off_and_is_carried_when_set():
    """The banner keys off via_bypass; a real login must never set it.

    The bypass itself signs in as a genuine account -- see test_accounts.py.
    This flag only marks that session so the UI keeps warning about it.
    """
    assert Principal(1, "gary", "Gary", Role.GM).via_bypass is False
    flagged = Principal(2, "beta-gm", "GM (beta bypass)", Role.GM, via_bypass=True)
    assert flagged.via_bypass is True
    assert flagged.is_gm
    assert not flagged.is_admin


@pytest.mark.parametrize("role", list(Role))
def test_every_role_has_a_rank(role):
    Principal(1, "u", "U", role).has_at_least(Role.ANON)
