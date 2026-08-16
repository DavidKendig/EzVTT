"""Undo and redo of board edits.

A checkpoint is a snapshot of the scene rather than a description of the change,
because an inverse operation that forgets a field fails silently, weeks later,
in front of a table. These tests are mostly about the awkward cases: undoing a
delete has to put the row back *as it was*, a drag must be one step rather than
sixty, and a new edit must throw away the redo branch. See ADR-020.
"""

import io

import pytest
from PIL import Image

from ezvtt import aoe, config, db, fog, media, state, undo


@pytest.fixture
def scene(tmp_path, monkeypatch):
    db_path = tmp_path / "undo.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)
    undo.forget()

    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1000), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    scene_id = state.scene_for_map(state.create_map(stored, "Tavern"))
    state.activate_scene(scene_id)

    conn = db.connect()
    asset_id = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h)
           VALUES ('Goblin', 'goblin.png', 'bundled', 1, 1)"""
    ).lastrowid
    conn.commit()

    yield {"id": scene_id, "asset": asset_id}
    undo.forget()
    db.close()


def labels(scene_id):
    return [t["label"] for t in state.list_tokens(scene_id)]


# --------------------------------------------------------------------------- #
# The basics
# --------------------------------------------------------------------------- #

def test_nothing_to_undo_says_so(scene):
    assert undo.undo(scene["id"]) is None
    assert undo.redo(scene["id"]) is None


def test_undoing_a_placement_removes_it(scene):
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)

    assert undo.undo(scene["id"]) == "place a token"
    assert state.list_tokens(scene["id"]) == []


def test_undoing_a_deletion_puts_the_token_back_whole(scene):
    """The case an inverse operation gets subtly wrong: id, z, owner, health,
    conditions -- all of it, not just the position."""
    token = state.place_token(scene["id"], scene["asset"], 3, 4, "token")
    state.update_token(token["id"], label="Captain", hp=7, hp_max=11,
                       conditions=["prone"], hidden=True, locked=True)
    before = state.get_token(token["id"])

    undo.checkpoint(scene["id"], "delete a token")
    state.delete_token(token["id"])
    undo.undo(scene["id"])

    assert state.get_token(token["id"]) == before


def test_redo_puts_it_back_again(scene):
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)
    undo.undo(scene["id"])

    assert undo.redo(scene["id"]) == "place a token"
    assert len(state.list_tokens(scene["id"])) == 1


def test_undo_and_redo_can_be_walked_back_and_forth(scene):
    for index in range(3):
        undo.checkpoint(scene["id"], f"place {index}")
        state.place_token(scene["id"], scene["asset"], index, 0)

    assert len(state.list_tokens(scene["id"])) == 3
    undo.undo(scene["id"])
    undo.undo(scene["id"])
    assert len(state.list_tokens(scene["id"])) == 1
    undo.redo(scene["id"])
    assert len(state.list_tokens(scene["id"])) == 2
    undo.undo(scene["id"])
    undo.undo(scene["id"])
    assert state.list_tokens(scene["id"]) == []


def test_a_new_edit_discards_the_redo_branch(scene):
    """Undoing and then doing something else means the future is gone --
    anything else is a tree, and a tree needs a UI nobody asked for."""
    undo.checkpoint(scene["id"], "place a goblin")
    first = state.place_token(scene["id"], scene["asset"], 1, 1)
    state.update_token(first["id"], label="Goblin")
    undo.undo(scene["id"])

    undo.checkpoint(scene["id"], "place an ogre")
    second = state.place_token(scene["id"], scene["asset"], 5, 5)
    state.update_token(second["id"], label="Ogre")

    assert undo.depth(scene["id"])["redo"] == 0
    assert undo.redo(scene["id"]) is None
    assert labels(scene["id"]) == ["Ogre"]


# --------------------------------------------------------------------------- #
# Drags
# --------------------------------------------------------------------------- #

def test_a_drag_is_one_step_not_sixty(scene):
    """token.update fires per animation frame; undo must take back the move,
    not one pixel of it."""
    token = state.place_token(scene["id"], scene["asset"], 0, 0)
    undo.forget(scene["id"])

    for step in range(1, 40):
        undo.checkpoint(scene["id"], f"move a token:{token['id']}")
        state.update_token(token["id"], x=step, y=step)

    assert undo.depth(scene["id"])["undo"] == 1
    undo.undo(scene["id"])
    assert (state.get_token(token["id"])["x"], state.get_token(token["id"])["y"]) == (0, 0)


def test_two_different_edits_do_not_coalesce(scene):
    token = state.place_token(scene["id"], scene["asset"], 0, 0)
    undo.forget(scene["id"])

    undo.checkpoint(scene["id"], f"move a token:{token['id']}")
    state.update_token(token["id"], x=5)
    undo.checkpoint(scene["id"], "change a token")
    state.update_token(token["id"], label="Named")

    assert undo.depth(scene["id"])["undo"] == 2
    undo.undo(scene["id"])
    assert state.get_token(token["id"])["label"] is None
    assert state.get_token(token["id"])["x"] == 5


def test_two_tokens_dragged_in_turn_are_two_steps(scene):
    """The label carries the token id, so moving one and then another does not
    collapse into a single checkpoint."""
    first = state.place_token(scene["id"], scene["asset"], 0, 0)
    second = state.place_token(scene["id"], scene["asset"], 1, 1)
    undo.forget(scene["id"])

    undo.checkpoint(scene["id"], f"move a token:{first['id']}")
    state.update_token(first["id"], x=8)
    undo.checkpoint(scene["id"], f"move a token:{second['id']}")
    state.update_token(second["id"], x=9)

    assert undo.depth(scene["id"])["undo"] == 2


# --------------------------------------------------------------------------- #
# Everything a checkpoint covers
# --------------------------------------------------------------------------- #

def test_fog_comes_back(scene):
    fog.set_all(scene["id"], False)
    undo.checkpoint(scene["id"], "brush the fog")
    fog.paint_rect(scene["id"], 0, 0, 5, 5, revealed=True)
    assert sum(fog.get(scene["id"])["cells"]) > 0

    undo.undo(scene["id"])

    assert sum(fog.get(scene["id"])["cells"]) == 0


def test_the_fog_version_keeps_climbing_through_an_undo(scene):
    """It is a cache key for the composited image players are served. Reusing a
    number would hand them the fog they had a moment ago."""
    fog.set_all(scene["id"], False)
    undo.checkpoint(scene["id"], "brush the fog")
    fog.paint_rect(scene["id"], 0, 0, 5, 5, revealed=True)
    before = fog.get(scene["id"])["version"]

    undo.undo(scene["id"])

    assert fog.get(scene["id"])["version"] > before


def test_templates_come_back(scene):
    aoe.place(scene["id"], "circle", 2, 2, 3, label="Fireball")
    undo.checkpoint(scene["id"], "remove a template")
    aoe.clear(scene["id"])

    undo.undo(scene["id"])

    assert [t["label"] for t in aoe.list_for(scene["id"])] == ["Fireball"]


def test_clearing_the_board_can_be_taken_back(scene):
    for index in range(5):
        state.place_token(scene["id"], scene["asset"], index, 0)
    undo.checkpoint(scene["id"], "clear the tokens")
    state.clear_tokens(scene["id"])

    undo.undo(scene["id"])

    assert len(state.list_tokens(scene["id"])) == 5


# --------------------------------------------------------------------------- #
# Bounds and bookkeeping
# --------------------------------------------------------------------------- #

def test_history_is_bounded(scene):
    for index in range(undo.MAX_DEPTH + 15):
        undo.checkpoint(scene["id"], f"edit {index}")
        state.place_token(scene["id"], scene["asset"], 0, 0)

    assert undo.depth(scene["id"])["undo"] == undo.MAX_DEPTH


def test_history_is_per_scene(scene):
    other = state.create_scene(1, "Elsewhere")
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)

    assert undo.depth(scene["id"])["undo"] == 1
    assert undo.depth(other)["undo"] == 0
    assert undo.undo(other) is None


def test_depth_describes_the_buttons(scene):
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)

    assert undo.depth(scene["id"]) == {
        "undo": 1, "redo": 0, "next_undo": "place a token", "next_redo": None,
    }

    undo.undo(scene["id"])
    assert undo.depth(scene["id"]) == {
        "undo": 0, "redo": 1, "next_undo": None, "next_redo": "place a token",
    }


def test_history_does_not_survive_being_forgotten(scene):
    """It is a property of the session, not of the campaign: a GM who reopens
    EzVTT expects the table restored, not an hour of edits still pending."""
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)

    undo.forget(scene["id"])

    assert undo.depth(scene["id"])["undo"] == 0
    assert len(state.list_tokens(scene["id"])) == 1


# --------------------------------------------------------------------------- #
# Bugs found after the fact
# --------------------------------------------------------------------------- #

def test_the_first_fog_stroke_on_a_scene_can_be_undone(scene):
    """There is no fog row until something creates one, so a checkpoint taken
    before the very first stroke captured no mask at all -- and the restore,
    finding nothing to put back, left the stroke exactly where it was."""
    assert db.connect().execute(
        "SELECT COUNT(*) AS n FROM fog WHERE scene_id = ?", (scene["id"],)
    ).fetchone()["n"] == 0

    undo.checkpoint(scene["id"], "brush the fog")
    fog.paint_rect(scene["id"], 0, 0, 4, 4, revealed=True)
    assert sum(fog.get(scene["id"])["cells"]) > 0

    undo.undo(scene["id"])

    assert sum(fog.get(scene["id"])["cells"]) == 0


def test_undo_survives_the_asset_being_deleted_from_the_library(scene):
    """Removing artwork is an ordinary thing to do, and it used to make the
    next undo fail its foreign key -- which closed the GM's socket rather than
    reporting anything."""
    token = state.place_token(scene["id"], scene["asset"], 3, 3)
    state.update_token(token["id"], label="Goblin")

    undo.checkpoint(scene["id"], "delete a token")
    state.delete_token(token["id"])
    db.connect().execute("DELETE FROM assets WHERE id = ?", (scene["asset"],))
    db.connect().commit()

    undo.undo(scene["id"])

    restored = state.get_token(token["id"])
    assert restored is not None
    assert restored["label"] == "Goblin"
    # It comes back without its picture, which is what the live schema does to
    # a token whose asset is deleted underneath it.
    assert restored["asset_id"] is None


def test_undoing_a_deletion_puts_the_creature_back_in_the_turn_order(scene):
    """The initiative row cascades away with the token. Restoring the token and
    not the row left the tracker silently one creature short."""
    from ezvtt import initiative

    token = state.place_token(scene["id"], scene["asset"], 1, 1, "token")
    state.update_token(token["id"], label="Goblin")
    initiative.add_tokens(scene["id"], [token["id"]])
    initiative.add(scene["id"], "Anya", value=12)
    initiative.start(scene["id"])

    undo.checkpoint(scene["id"], "delete a token")
    state.delete_token(token["id"])
    assert [e["label"] for e in initiative.get(scene["id"])["entries"]] == ["Anya"]

    undo.undo(scene["id"])

    assert [e["label"] for e in initiative.get(scene["id"])["entries"]] == ["Anya", "Goblin"]


def test_an_undo_into_a_scene_that_has_gone_is_refused_not_raised(scene):
    """A database error travelling up from an intent closes the GM's socket.

    The snapshot has to contain something for this to bite: restoring nothing
    into a missing scene inserts no rows and breaks no foreign key.
    """
    state.place_token(scene["id"], scene["asset"], 1, 1)
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 2, 2)

    db.connect().execute("DELETE FROM scenes WHERE id = ?", (scene["id"],))
    db.connect().commit()

    with pytest.raises(ValueError, match="cannot be undone"):
        undo.undo(scene["id"])


def test_deleting_a_scene_drops_its_history(scene):
    """Otherwise it sits in memory for the life of the process, describing rows
    that no longer exist."""
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)
    assert undo.depth(scene["id"])["undo"] == 1

    state.delete_scene(scene["id"])

    assert undo.depth(scene["id"])["undo"] == 0


def test_deleting_a_map_drops_the_history_of_every_scene_on_it(scene):
    other = state.create_scene(1, "Elsewhere")
    undo.checkpoint(scene["id"], "place a token")
    state.place_token(scene["id"], scene["asset"], 1, 1)
    undo.checkpoint(other, "place a token")

    state.delete_map(1)

    assert undo.depth(scene["id"])["undo"] == 0
    assert undo.depth(other)["undo"] == 0
