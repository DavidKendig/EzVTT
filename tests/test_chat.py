"""Chat storage and, above all, who is allowed to read what.

``visible_to`` is used for both live delivery and history replay. If those ever
disagree, a "private" roll a player did not see live turns up in their log after
a reconnect -- so the same function is tested against both paths here.
"""

import pytest

from ezvtt import auth, chat, config, db
from ezvtt.auth import Role
from ezvtt.dice import DiceError


@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "chat.db")
    db.close()
    db.migrate(tmp_path / "chat.db")

    people = {
        "gm": auth.create_user("gary", "a long enough password", Role.GM, "Gary"),
        "p1": auth.create_user("alice", "a long enough password", Role.PLAYER, "Alice"),
        "p2": auth.create_user("bob", "a long enough password", Role.PLAYER, "Bob"),
    }
    yield people
    db.close()


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #

def test_public_chat_is_visible_to_everyone(table):
    message = chat.say(table["p1"], "Alice", "I open the door")
    assert chat.visible_to(message, table["p1"], False)
    assert chat.visible_to(message, table["p2"], False)
    assert chat.visible_to(message, table["gm"], True)
    assert chat.visible_to(message, None, False)


def test_public_roll_is_visible_to_everyone(table):
    message = chat.roll(table["p1"], "Alice", "d20")
    assert chat.visible_to(message, table["p2"], False)


def test_private_roll_reaches_the_roller_and_gms_only(table):
    message = chat.roll(table["p1"], "Alice", "d20", private=True)
    assert chat.visible_to(message, table["p1"], False), "the roller"
    assert chat.visible_to(message, table["gm"], True), "the GM"
    assert not chat.visible_to(message, table["p2"], False), "another player"
    assert not chat.visible_to(message, None, False), "anonymous"


def test_whisper_reaches_only_the_two_parties(table):
    message = chat.whisper(table["gm"], "Gary", table["p1"], "the door is trapped")
    assert chat.visible_to(message, table["gm"], True), "the sender"
    assert chat.visible_to(message, table["p1"], False), "the recipient"
    assert not chat.visible_to(message, table["p2"], False), "a bystander"


def test_a_gm_cannot_read_a_whisper_they_are_not_part_of(table):
    """A GM sees private rolls because they asked for them. Reading other
    people's messages is a different thing."""
    other_gm = auth.create_user("dave", "a long enough password", Role.GM, "Dave")
    message = chat.whisper(table["p1"], "Alice", table["p2"], "meet me outside")
    assert not chat.visible_to(message, other_gm, True)


# --------------------------------------------------------------------------- #
# History replays exactly what was delivered
# --------------------------------------------------------------------------- #

def test_history_matches_live_visibility(table):
    chat.say(table["p1"], "Alice", "public words")
    chat.roll(table["p1"], "Alice", "d20", private=True)
    chat.whisper(table["gm"], "Gary", table["p1"], "psst")
    chat.roll(table["p2"], "Bob", "2d6")

    for user_id, is_gm in [(table["p1"], False), (table["p2"], False), (table["gm"], True)]:
        replayed = chat.history(user_id, is_gm)
        for message in replayed:
            assert chat.visible_to(message, user_id, is_gm), (
                f"history gave {user_id} a message live delivery would not"
            )


def test_a_bystanders_history_excludes_private_traffic(table):
    chat.say(table["p1"], "Alice", "public words")
    chat.roll(table["p1"], "Alice", "d20", private=True)
    chat.whisper(table["gm"], "Gary", table["p1"], "secret passage")

    replayed = chat.history(table["p2"], False)
    bodies = " ".join(m["body"] for m in replayed)
    assert "public words" in bodies
    assert "secret passage" not in bodies
    assert not any(m["private"] for m in replayed)


def test_the_gm_history_includes_private_rolls(table):
    chat.roll(table["p1"], "Alice", "d20", private=True)
    replayed = chat.history(table["gm"], True)
    assert any(m["private"] and m["kind"] == "roll" for m in replayed)


def test_history_is_oldest_first(table):
    for i in range(5):
        chat.say(table["p1"], "Alice", f"message {i}")
    bodies = [m["body"] for m in chat.history(table["p1"], False)]
    assert bodies == [f"message {i}" for i in range(5)]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def test_a_roll_stores_its_full_detail(table):
    message = chat.roll(table["p1"], "Alice", "2d6+3")
    assert message["kind"] == "roll"
    assert message["roll"]["notation"] == "2d6+3"
    assert len(message["roll"]["terms"][0]["dice"]) == 2
    assert 5 <= message["roll"]["total"] <= 15


def test_the_stored_body_is_the_notation(table):
    """There is nowhere for a client to put a claimed result."""
    assert chat.roll(table["p1"], "Alice", "d20")["body"] == "d20"


def test_bad_notation_raises_rather_than_posting(table):
    with pytest.raises(DiceError):
        chat.roll(table["p1"], "Alice", "9999d9")
    assert chat.history(table["gm"], True) == []


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_messages_are_refused(table, blank):
    with pytest.raises(ValueError):
        chat.say(table["p1"], "Alice", blank)
    with pytest.raises(ValueError):
        chat.whisper(table["p1"], "Alice", table["p2"], blank)


def test_whispering_at_nobody_is_refused(table):
    with pytest.raises(ValueError, match="no such person"):
        chat.whisper(table["p1"], "Alice", 9999, "hello?")


def test_long_messages_are_truncated_not_rejected(table):
    message = chat.say(table["p1"], "Alice", "x" * 5000)
    assert len(message["body"]) <= chat.MAX_MESSAGE_LENGTH


def test_history_is_capped(table):
    """A long campaign must not replay an unbounded log on every connect."""
    for i in range(chat.HISTORY_LIMIT + 40):
        chat.say(table["p1"], "Alice", f"m{i}")

    total = db.connect().execute("SELECT COUNT(*) AS n FROM chat_log").fetchone()["n"]
    assert total <= chat.HISTORY_LIMIT

    # The oldest were dropped, the newest kept.
    bodies = [m["body"] for m in chat.history(table["p1"], False, limit=10)]
    assert bodies[-1] == f"m{chat.HISTORY_LIMIT + 39}"


def test_attribution_survives_account_deletion(table):
    """History stays readable after a player leaves the campaign."""
    chat.say(table["p2"], "Bob", "I was here")
    auth.delete_user(table["p2"])

    replayed = chat.history(table["gm"], True)
    kept = [m for m in replayed if m["body"] == "I was here"]
    assert kept and kept[0]["author"] == "Bob"


def test_roster_lists_active_people(table):
    names = {p["username"] for p in chat.roster()}
    assert {"gary", "alice", "bob"} <= names

    auth.set_active(table["p2"], False)
    assert "bob" not in {p["username"] for p in chat.roster()}


def test_clear(table):
    chat.say(table["p1"], "Alice", "hello")
    assert chat.clear() >= 1
    assert chat.history(table["gm"], True) == []
