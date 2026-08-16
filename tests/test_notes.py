"""Player notes: private pads, a shared board, and who may read which.

The id-guessing test is the one that matters: a note absent from a player's
list must also be unfetchable by asking for it directly.
"""

import pytest

from ezvtt import auth, config, db, notes
from ezvtt.auth import Role


@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "notes.db")
    db.close()
    db.migrate(tmp_path / "notes.db")
    people = {
        "gm": auth.create_user("gary", "a long enough password", Role.GM, "Gary"),
        "alice": auth.create_user("alice", "a long enough password", Role.PLAYER, "Alice"),
        "bob": auth.create_user("bob", "a long enough password", Role.PLAYER, "Bob"),
    }
    yield people
    db.close()


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #

def test_a_private_note_belongs_to_its_author_alone(table):
    note = notes.create(table["alice"], "My plan", "Steal the crown", "private")

    assert notes.get(note["id"], table["alice"], is_gm=False) is not None
    assert notes.get(note["id"], table["bob"], is_gm=False) is None
    assert notes.get(note["id"], table["gm"], is_gm=True) is None, (
        "a GM should not read a player's private pad"
    )


def test_a_public_note_is_on_the_shared_board(table):
    note = notes.create(table["alice"], "Party funds", "142 gp", "public")
    for reader, is_gm in [(table["bob"], False), (table["gm"], True)]:
        assert notes.get(note["id"], reader, is_gm) is not None


def test_a_gm_visible_note_reaches_the_gm_but_not_other_players(table):
    """For a question you want answered but not read aloud."""
    note = notes.create(table["alice"], "Question", "Can I sense the lie?", "gm")

    assert notes.get(note["id"], table["alice"], is_gm=False) is not None
    assert notes.get(note["id"], table["gm"], is_gm=True) is not None
    assert notes.get(note["id"], table["bob"], is_gm=False) is None


def test_listing_and_fetching_agree(table):
    """A note absent from the list must not be fetchable by guessing its id."""
    notes.create(table["alice"], "Private", "secret", "private")
    notes.create(table["alice"], "Public", "shared", "public")
    notes.create(table["alice"], "For the GM", "psst", "gm")
    notes.create(table["bob"], "Bob private", "mine", "private")

    for reader, is_gm in [(table["alice"], False), (table["bob"], False),
                          (table["gm"], True)]:
        listed = {n["id"] for n in notes.list_notes(reader, is_gm)}
        for note_id in range(1, 5):
            fetched = notes.get(note_id, reader, is_gm)
            assert (fetched is not None) == (note_id in listed), (
                f"note {note_id} listed={note_id in listed} but "
                f"fetch={'ok' if fetched else 'refused'}"
            )


def test_a_player_sees_only_their_own_and_the_shared(table):
    notes.create(table["alice"], "Alice private", "", "private")
    notes.create(table["bob"], "Bob private", "", "private")
    notes.create(table["bob"], "Board", "", "public")

    titles = {n["title"] for n in notes.list_notes(table["alice"], is_gm=False)}
    assert titles == {"Alice private", "Board"}


# --------------------------------------------------------------------------- #
# Editing
# --------------------------------------------------------------------------- #

def test_the_author_may_edit(table):
    note = notes.create(table["alice"], "Draft", "one", "private")
    updated = notes.update(note["id"], table["alice"], False,
                           title="Final", body="two", visibility="public")
    assert updated["title"] == "Final"
    assert updated["body"] == "two"
    assert updated["visibility"] == "public"


def test_nobody_else_may_edit_including_the_gm(table):
    """A GM can read the shared board; rewriting someone's note is different."""
    note = notes.create(table["alice"], "Mine", "", "public")
    for editor, is_gm in [(table["bob"], False), (table["gm"], True)]:
        with pytest.raises(PermissionError):
            notes.update(note["id"], editor, is_gm, body="changed")


def test_the_author_may_delete(table):
    note = notes.create(table["alice"], "Scratch", "", "private")
    assert notes.delete(note["id"], table["alice"], is_gm=False) is True
    assert notes.get(note["id"], table["alice"], is_gm=False) is None


def test_a_gm_may_tidy_the_board_but_a_player_may_not(table):
    first = notes.create(table["alice"], "Spam", "", "public")
    second = notes.create(table["alice"], "Spam", "", "public")

    with pytest.raises(PermissionError):
        notes.delete(first["id"], table["bob"], is_gm=False)
    assert notes.delete(second["id"], table["gm"], is_gm=True) is True


def test_unknown_visibility_is_refused(table):
    with pytest.raises(ValueError):
        notes.create(table["alice"], "x", "", "everyone")

    note = notes.create(table["alice"], "x", "", "private")
    with pytest.raises(ValueError):
        notes.update(note["id"], table["alice"], False, visibility="everyone")


def test_a_blank_title_becomes_untitled(table):
    assert notes.create(table["alice"], "   ", "body")["title"] == "Untitled"


def test_oversized_content_is_truncated_not_rejected(table):
    note = notes.create(table["alice"], "t" * 500, "b" * 100_000)
    assert len(note["title"]) <= notes.MAX_TITLE
    assert len(note["body"]) <= notes.MAX_BODY


def test_editing_a_missing_note_returns_none(table):
    assert notes.update(9999, table["alice"], False, title="x") is None
    assert notes.delete(9999, table["alice"], False) is False


def test_notes_carry_their_author_name(table):
    note = notes.create(table["alice"], "Hello", "", "public")
    assert notes.get(note["id"], table["bob"], is_gm=False)["author"] == "Alice"
