"""Several scenes over one map, and what each audience is told about them.

Two rules carry the feature. A copy of a scene must be independent of its
source, or duplicating "the tavern" to prep the next fight would drag every
later change back into the original. And a scene *name* is GM prep -- "Ambush at
the bridge" must not reach a player's browser, the same rule fog and hidden
tokens follow. See ADR-004.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, state


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A fresh database, media root, and one asset to place."""
    db_path = tmp_path / "scenes.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)

    conn = db.connect()
    cursor = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h)
           VALUES ('Barrel', 'barrel.png', 'bundled', 1, 1)"""
    )
    conn.commit()

    yield {"asset_id": cursor.lastrowid}
    db.close()


def make_map(name="Tavern", width=1400, height=1000) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (80, 60, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), f"{name}.png", config.MAPS_DIR)
    return state.create_map(stored, name)


# --------------------------------------------------------------------------- #
# Several per map
# --------------------------------------------------------------------------- #

def test_several_scenes_can_share_one_map(board):
    map_id = make_map()
    first = state.scene_for_map(map_id)
    second = state.create_scene(map_id, "The tavern, after the fight")

    assert second != first
    assert {s["id"] for s in state.list_scenes()} == {first, second}


def test_creating_a_scene_over_a_missing_map_is_refused(board):
    with pytest.raises(ValueError, match="map"):
        state.create_scene(9999, "Nowhere")


def test_a_nameless_scene_still_gets_a_name(board):
    map_id = make_map()
    scene_id = state.create_scene(map_id, "   ")
    assert state.get_scene(scene_id)["name"] == "Scene"


def test_clicking_a_map_returns_to_the_scene_last_run(board):
    """Otherwise a GM is taken back to the encounter they finished last week."""
    tavern = make_map("Tavern")
    cellar = make_map("Cellar")

    first = state.scene_for_map(tavern)
    second = state.create_scene(tavern, "The tavern, after the fight")

    state.activate_scene(first)
    state.activate_scene(second)
    state.activate_scene(state.scene_for_map(cellar))

    assert state.scene_for_map(tavern) == second
    assert state.scene_for_map(tavern) != first


def test_scene_for_map_is_stable_before_anything_is_activated(board):
    map_id = make_map()
    assert state.scene_for_map(map_id) == state.scene_for_map(map_id)


# --------------------------------------------------------------------------- #
# Copying
# --------------------------------------------------------------------------- #

def test_a_copy_carries_the_layout_and_the_fog(board):
    """A duplicate of a half-explored dungeon that arrives concealed is a
    different scene, not a copy of this one."""
    map_id = make_map()
    scene_id = state.scene_for_map(map_id)
    state.place_token(scene_id, board["asset_id"], 3, 4)
    state.place_token(scene_id, board["asset_id"], 7, 2)
    fog.paint_rect(scene_id, 0, 0, 4, 3, revealed=True)

    copy_id = state.duplicate_scene(scene_id)

    assert copy_id is not None
    assert [(t["x"], t["y"]) for t in state.list_tokens(copy_id)] == [(3, 4), (7, 2)]
    assert fog.get(copy_id)["cells"] == fog.get(scene_id)["cells"]
    assert state.get_scene(copy_id)["map_id"] == map_id


def test_a_copy_is_independent_of_its_source(board):
    map_id = make_map()
    scene_id = state.scene_for_map(map_id)
    original = state.place_token(scene_id, board["asset_id"], 3, 4)

    copy_id = state.duplicate_scene(scene_id)
    copied = state.list_tokens(copy_id)[0]

    assert copied["id"] != original["id"]

    state.update_token(copied["id"], x=10, y=10)
    fog.set_all(copy_id, True)

    assert state.get_token(original["id"])["x"] == 3
    assert not any(fog.get(scene_id)["cells"])


def test_a_copy_is_not_put_on_the_table(board):
    """Prepping the next encounter must not change what the room is looking at."""
    map_id = make_map()
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    copy_id = state.duplicate_scene(scene_id)

    assert state.active_scene()["id"] == scene_id
    assert state.get_scene(copy_id)["active"] is False


def test_a_copy_is_named_after_its_source(board):
    map_id = make_map("Tavern")
    copy_id = state.duplicate_scene(state.scene_for_map(map_id))
    assert state.get_scene(copy_id)["name"] == "Tavern copy"

    named = state.duplicate_scene(state.scene_for_map(map_id), "Round two")
    assert state.get_scene(named)["name"] == "Round two"


def test_duplicating_a_missing_scene_returns_none(board):
    assert state.duplicate_scene(9999) is None


# --------------------------------------------------------------------------- #
# Renaming and deleting
# --------------------------------------------------------------------------- #

def test_rename(board):
    scene_id = state.scene_for_map(make_map())
    assert state.rename_scene(scene_id, "Goblin ambush")
    assert state.get_scene(scene_id)["name"] == "Goblin ambush"


def test_rename_rejects_an_empty_name(board):
    scene_id = state.scene_for_map(make_map())
    with pytest.raises(ValueError):
        state.rename_scene(scene_id, "   ")


def test_renaming_a_missing_scene_reports_it(board):
    assert state.rename_scene(9999, "Anywhere") is False


def test_deleting_a_scene_keeps_the_map_and_the_other_scenes(board):
    map_id = make_map()
    first = state.scene_for_map(map_id)
    second = state.create_scene(map_id, "Round two")

    assert state.delete_scene(first)
    assert state.get_map(map_id) is not None
    assert [s["id"] for s in state.list_scenes()] == [second]


def test_deleting_the_active_scene_empties_the_table(board):
    """Switching the table to some other encounter unasked is worse than
    an empty board."""
    map_id = make_map()
    first = state.scene_for_map(map_id)
    state.create_scene(map_id, "Round two")
    state.activate_scene(first)

    state.delete_scene(first)

    assert state.active_scene() is None


def test_deleting_a_scene_takes_its_tokens_with_it(board):
    map_id = make_map()
    scene_id = state.scene_for_map(map_id)
    token = state.place_token(scene_id, board["asset_id"], 1, 1)

    state.delete_scene(scene_id)

    assert state.get_token(token["id"]) is None


def test_deleting_a_missing_scene_reports_it(board):
    assert state.delete_scene(9999) is False


# --------------------------------------------------------------------------- #
# What each audience is told
# --------------------------------------------------------------------------- #

def test_the_scene_list_says_which_map_and_how_much_is_on_it(board):
    map_id = make_map("Tavern")
    scene_id = state.scene_for_map(map_id)
    state.place_token(scene_id, board["asset_id"], 1, 1)
    state.place_token(scene_id, board["asset_id"], 2, 2)

    scene = state.list_scenes()[0]
    assert scene["map_name"] == "Tavern"
    assert scene["token_count"] == 2
    assert scene["thumb_url"].startswith("/media/thumbs/maps/")


def test_gm_snapshot_lists_every_scene(board):
    tavern = make_map("Tavern")
    state.scene_for_map(tavern)
    state.create_scene(tavern, "Round two")
    state.scene_for_map(make_map("Cellar"))

    snapshot = state.snapshot(for_gm=True)
    # Newest map first, matching the library above it, with a map's own scenes
    # in the order they were prepped.
    assert [s["name"] for s in snapshot["scenes"]] == ["Cellar", "Tavern", "Round two"]


def test_gm_snapshot_lists_scenes_with_nothing_on_the_table(board):
    """The switcher is how a GM gets back to a table they cleared."""
    state.scene_for_map(make_map("Tavern"))

    snapshot = state.snapshot(for_gm=True)
    assert snapshot["scene"] is None
    assert len(snapshot["scenes"]) == 1


def test_player_snapshot_omits_the_scene_list(board):
    state.create_scene(make_map("Tavern"), "The traitor reveals himself")

    snapshot = state.snapshot(for_gm=False)

    assert "scenes" not in snapshot
    assert "traitor" not in repr(snapshot)


def test_player_snapshot_omits_the_active_scene_name(board):
    """The name of what they are looking at is prep, and prep is a spoiler."""
    map_id = make_map("Bridge")
    scene_id = state.create_scene(map_id, "Ambush at the bridge")
    state.activate_scene(scene_id)

    player = state.snapshot(for_gm=False)

    assert player["scene"]["id"] == scene_id
    assert "name" not in player["scene"]
    assert "Ambush" not in repr(player)

    # The GM, who wrote it, still sees it.
    assert state.snapshot(for_gm=True)["scene"]["name"] == "Ambush at the bridge"
