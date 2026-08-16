"""Who may move a token.

The GM moves anything. Everyone else moves exactly what is theirs, and only
while it is on the table in front of them. The awkward cases are the ones that
matter: a locked token is pinned down on purpose, and a hidden one must not be
findable by feel. Every refusal says the same sentence, because *which* refusal
applied is itself information about the board. See ADR-021.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A scene, an asset, and two players — Anya owns a token, Brand does not."""
    db_path = tmp_path / "ownership.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)

    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1000), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    scene_id = state.scene_for_map(state.create_map(stored, "Tavern"))
    state.activate_scene(scene_id)
    fog.set_all(scene_id, True)

    conn = db.connect()
    asset_id = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h)
           VALUES ('Adventurer', 'a.png', 'bundled', 1, 1)"""
    ).lastrowid
    people = {}
    for username, name in (("anya", "Anya"), ("brand", "Brand"), ("gary", "Gary")):
        people[username] = conn.execute(
            """INSERT INTO users (username, display_name, pw_hash, pw_salt,
                                  pw_n, pw_r, pw_p, role)
               VALUES (?, ?, 'h', 's', 1, 1, 1, 'player')""",
            (username, name),
        ).lastrowid
    conn.commit()

    token = state.place_token(scene_id, asset_id, 2, 2, "token")
    state.update_token(token["id"], label="Anya", owner_user_id=people["anya"])

    yield {"scene": scene_id, "asset": asset_id, "token": token["id"], **people}
    db.close()


def token(table, **changes):
    if changes:
        state.update_token(table["token"], **changes)
    return state.get_token(table["token"])


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #

def test_the_owner_may_move_their_own_token(table):
    assert state.may_move(token(table), table["anya"], is_gm=False)


def test_nobody_else_may(table):
    assert not state.may_move(token(table), table["brand"], is_gm=False)


def test_an_unowned_token_belongs_to_nobody_not_everybody(table):
    loose = token(table, owner_user_id=None)
    assert not state.may_move(loose, table["anya"], is_gm=False)
    assert not state.may_move(loose, table["brand"], is_gm=False)


def test_the_gm_may_move_anything(table):
    assert state.may_move(token(table), None, is_gm=True)
    assert state.may_move(token(table, owner_user_id=None), 999, is_gm=True)
    assert state.may_move(token(table, locked=True, hidden=True), None, is_gm=True)


def test_locked_means_locked_even_for_the_owner(table):
    """It is how a GM pins the furniture down, and dragging the tavern's bar
    across the room is the thing it exists to stop."""
    assert not state.may_move(token(table, locked=True), table["anya"], is_gm=False)


def test_a_hidden_token_cannot_be_felt_for(table):
    """It is absent from their payload for a reason; being able to move it
    would give it back."""
    assert not state.may_move(token(table, hidden=True), table["anya"], is_gm=False)


def test_an_anonymous_viewer_moves_nothing(table):
    assert not state.may_move(token(table), None, is_gm=False)


# --------------------------------------------------------------------------- #
# Assigning an owner
# --------------------------------------------------------------------------- #

def test_ownership_round_trips_and_can_be_taken_back(table):
    assert token(table)["owner_user_id"] == table["anya"]
    assert token(table, owner_user_id=None)["owner_user_id"] is None


def test_an_owner_who_is_not_an_account_is_refused(table):
    """A token owned by an id that is not a person is a token nobody can move
    and the GM cannot see why."""
    with pytest.raises(ValueError, match="no such person"):
        state.update_token(table["token"], owner_user_id=9999)


def test_a_disabled_account_cannot_be_given_a_token(table):
    db.connect().execute("UPDATE users SET is_active = 0 WHERE id = ?", (table["brand"],))
    db.connect().commit()

    with pytest.raises(ValueError, match="no such person"):
        state.update_token(table["token"], owner_user_id=table["brand"])


def test_the_gm_is_told_who_a_token_can_be_given_to(table):
    people = state.snapshot(for_gm=True)["people"]

    assert [p["display_name"] for p in people] == ["Anya", "Brand", "Gary"]
    # The account list is not a player's business.
    assert "people" not in state.snapshot(for_gm=False, viewer_id=table["anya"])


# --------------------------------------------------------------------------- #
# What the owner gets out of it
# --------------------------------------------------------------------------- #

def test_the_owner_sees_their_own_tokens_numbers(table):
    """Ownership decides two things, and this is the other one. See ADR-017."""
    state.update_token(table["token"], hp=9, hp_max=14)

    mine = state.snapshot(for_gm=False, viewer_id=table["anya"])["tokens"][0]
    theirs = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert (mine["hp"], mine["hp_max"]) == (9, 14)
    assert "hp" not in theirs


def test_a_move_is_still_clamped_to_the_map(table):
    """A player's drag goes through the same validation the GM's does: a stale
    zoom turning a screen point into a wild grid coordinate must not lose the
    token off the board."""
    moved = state.update_token(table["token"], x=99_999, y=99_999)
    cols, rows = state.scene_bounds(table["scene"])

    assert moved["x"] <= cols
    assert moved["y"] <= rows
