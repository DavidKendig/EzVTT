"""Account creation, authentication, sessions, and CSRF."""

import pytest

from ezvtt import auth, config, db
from ezvtt.auth import Role


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "accounts.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.close()
    db.migrate(db_path)
    yield db_path
    db.close()


# --------------------------------------------------------------------------- #
# Creation and validation
# --------------------------------------------------------------------------- #

def test_create_and_authenticate(accounts):
    auth.create_user("gary", "correct horse battery", Role.ADMIN, "Gary")

    principal = auth.authenticate("gary", "correct horse battery")
    assert principal is not None
    assert principal.role is Role.ADMIN
    assert principal.display_name == "Gary"
    assert principal.is_admin


def test_wrong_password_is_refused(accounts):
    auth.create_user("gary", "correct horse battery", Role.GM)
    assert auth.authenticate("gary", "Correct horse battery") is None
    assert auth.authenticate("gary", "") is None


def test_unknown_user_is_refused(accounts):
    assert auth.authenticate("nobody", "whatever password") is None


def test_duplicate_username_is_refused(accounts):
    auth.create_user("gary", "a long enough password", Role.GM)
    with pytest.raises(ValueError, match="already exists"):
        auth.create_user("gary", "another long password", Role.PLAYER)


def test_usernames_are_case_insensitively_unique(accounts):
    auth.create_user("Gary", "a long enough password", Role.GM)
    with pytest.raises(ValueError, match="already exists"):
        auth.create_user("gary", "another long password", Role.PLAYER)


@pytest.mark.parametrize("bad", ["", " ", "a", "!!", "no/slashes", "x" * 40, "<script>"])
def test_bad_usernames_are_refused(accounts, bad):
    with pytest.raises(ValueError, match="[Uu]sernames"):
        auth.create_user(bad, "a long enough password", Role.PLAYER)


@pytest.mark.parametrize("bad", ["", "short", "1234567"])
def test_short_passwords_are_refused(accounts, bad):
    with pytest.raises(ValueError, match="at least"):
        auth.create_user("someone", bad, Role.PLAYER)


def test_anonymous_role_cannot_be_created(accounts):
    with pytest.raises(ValueError):
        auth.create_user("ghost", "a long enough password", Role.ANON)


def test_inactive_accounts_cannot_sign_in(accounts):
    auth.create_user("gary", "a long enough password", Role.ADMIN)
    bob = auth.create_user("bob", "a long enough password", Role.PLAYER)

    assert auth.authenticate("bob", "a long enough password") is not None
    auth.set_active(bob, False)
    assert auth.authenticate("bob", "a long enough password") is None

    auth.set_active(bob, True)
    assert auth.authenticate("bob", "a long enough password") is not None


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

def test_session_round_trip(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.GM)
    signed = auth.create_session(user_id)

    token = auth.unsign(signed)
    assert token is not None

    principal = auth.principal_for_token(token)
    assert principal is not None
    assert principal.user_id == user_id


def test_a_tampered_cookie_is_rejected(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.GM)
    signed = auth.create_session(user_id)

    value, _, signature = signed.rpartition(".")

    # Flip the last character to something it demonstrably is not. Substituting
    # a fixed digit is a one-in-sixteen chance of rebuilding the *original*
    # cookie and asserting that a valid session is rejected.
    flipped = signature[:-1] + ("1" if signature[-1] == "0" else "0")
    assert flipped != signature

    assert auth.unsign(f"{value}x.{signature}") is None   # value changed
    assert auth.unsign(f"{value}.{flipped}") is None      # signature changed
    assert auth.unsign(value) is None                     # signature removed
    assert auth.unsign("") is None
    assert auth.unsign(".") is None


def test_destroying_a_session_ends_it(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.GM)
    signed = auth.create_session(user_id)
    auth.destroy_session(signed)
    assert auth.principal_for_token(auth.unsign(signed) or "") is None


def test_changing_a_password_signs_out_everywhere(accounts):
    """The whole point of a reset after a suspected compromise."""
    user_id = auth.create_user("gary", "a long enough password", Role.GM)
    first = auth.create_session(user_id)
    second = auth.create_session(user_id)

    auth.set_password(user_id, "a different long password")

    assert auth.principal_for_token(auth.unsign(first) or "") is None
    assert auth.principal_for_token(auth.unsign(second) or "") is None
    assert auth.authenticate("gary", "a different long password") is not None


def test_deactivating_signs_out_everywhere(accounts):
    auth.create_user("admin", "a long enough password", Role.ADMIN)
    user_id = auth.create_user("bob", "a long enough password", Role.PLAYER)
    signed = auth.create_session(user_id)

    auth.set_active(user_id, False)
    assert auth.principal_for_token(auth.unsign(signed) or "") is None


def test_expired_sessions_are_purged(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.GM)
    conn = db.connect()
    conn.execute(
        """INSERT INTO sessions (token, user_id, expires_at)
           VALUES ('stale', ?, datetime('now', '-1 day'))""",
        (user_id,),
    )
    conn.commit()

    assert auth.principal_for_token("stale") is None
    assert auth.purge_expired_sessions() == 1


# --------------------------------------------------------------------------- #
# The last admin
# --------------------------------------------------------------------------- #

def test_the_only_admin_cannot_be_demoted(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.ADMIN)
    with pytest.raises(ValueError, match="only admin"):
        auth.set_role(user_id, Role.PLAYER)


def test_the_only_admin_cannot_be_deactivated_or_deleted(accounts):
    user_id = auth.create_user("gary", "a long enough password", Role.ADMIN)
    with pytest.raises(ValueError, match="only admin"):
        auth.set_active(user_id, False)
    with pytest.raises(ValueError, match="only admin"):
        auth.delete_user(user_id)


def test_an_admin_can_be_demoted_once_another_exists(accounts):
    first = auth.create_user("gary", "a long enough password", Role.ADMIN)
    auth.create_user("dave", "a long enough password", Role.ADMIN)
    auth.set_role(first, Role.PLAYER)
    assert auth.get_user(first)["role"] == "player"


# --------------------------------------------------------------------------- #
# First run
# --------------------------------------------------------------------------- #

def test_first_run_until_an_admin_exists(accounts):
    assert db.is_first_run() is True
    auth.create_user("bob", "a long enough password", Role.PLAYER)
    assert db.is_first_run() is True, "a player must not satisfy first run"
    auth.create_user("gm", "a long enough password", Role.GM)
    assert db.is_first_run() is True, "a GM must not satisfy first run"
    auth.create_user("gary", "a long enough password", Role.ADMIN)
    assert db.is_first_run() is False


# --------------------------------------------------------------------------- #
# The bypass account
# --------------------------------------------------------------------------- #

def test_bypass_account_is_a_real_gm_row(accounts):
    user_id = auth.ensure_bypass_account()
    user = auth.get_user(user_id)
    assert user["username"] == auth.BYPASS_USERNAME
    assert user["role"] == Role.GM.value


def test_bypass_account_is_created_once(accounts):
    assert auth.ensure_bypass_account() == auth.ensure_bypass_account()


def test_bypass_account_does_not_satisfy_first_run(accounts):
    """Skipping setup must not count as having created an administrator."""
    auth.ensure_bypass_account()
    assert db.is_first_run() is True


def test_bypass_account_has_no_usable_password(accounts):
    """The only way in is the button, which the run mode can refuse."""
    auth.ensure_bypass_account()
    for guess in ("", "beta-gm", "password", "bypass"):
        assert auth.authenticate(auth.BYPASS_USERNAME, guess) is None


def test_is_bypass_principal(accounts):
    user_id = auth.ensure_bypass_account()
    conn = db.connect()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    principal = auth.Principal(user_id, row["username"], row["display_name"], Role.GM)
    assert auth.is_bypass_principal(principal)

    other = auth.Principal(2, "gary", "Gary", Role.GM)
    assert not auth.is_bypass_principal(other)


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #

def test_csrf_matching_pair_is_accepted():
    token = auth.new_csrf_token()
    assert auth.csrf_valid(token, token)


@pytest.mark.parametrize("cookie, form", [
    ("abc", "def"),      # mismatched
    (None, "abc"),       # no cookie
    ("abc", None),       # no form field
    (None, None),
    ("", ""),            # empty must not count as a match
    ("abc", ""),
])
def test_csrf_rejects_anything_else(cookie, form):
    assert not auth.csrf_valid(cookie, form)


def test_csrf_tokens_are_unpredictable():
    tokens = {auth.new_csrf_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) > 20 for t in tokens)
