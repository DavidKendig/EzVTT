"""Token placement, mutation, and the hidden-token privacy rule.

The visibility tests are the important ones. A hidden token must be *absent*
from a player's payload, not flagged in it -- a flag is a spoiler to anyone who
opens developer tools. See ADR-004.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, media, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A database with one map on the table and one asset in the library."""
    db_path = tmp_path / "tokens.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    db.close()
    db.migrate(db_path)

    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1000), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Tavern")
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    conn = db.connect()
    cursor = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h, category)
           VALUES ('Anvil', 'anvil.png', 'bundled', 2, 1, 'Tools & industry')"""
    )
    conn.commit()

    # A new scene starts fully concealed, so a player would see no tokens at
    # all. These tests are about tokens; fog has its own file. Light the map.
    from ezvtt import fog

    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    fog.set_all(scene_id, True)

    yield {"scene_id": scene_id, "asset_id": cursor.lastrowid, "map_id": map_id}
    db.close()


# --------------------------------------------------------------------------- #
# Placement
# --------------------------------------------------------------------------- #

def test_placed_token_inherits_the_asset_footprint(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 4, 6)
    assert (token["grid_w"], token["grid_h"]) == (2, 1)
    assert (token["x"], token["y"]) == (4, 6)
    assert token["layer"] == "object"
    assert token["name"] == "Anvil"
    assert token["url"] == "/media/bundled/anvil.png"


def test_new_tokens_stack_on_top_of_their_layer(table):
    first = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    second = state.place_token(table["scene_id"], table["asset_id"], 1, 1)
    assert second["z"] > first["z"]


def test_z_is_tracked_per_layer(table):
    obj = state.place_token(table["scene_id"], table["asset_id"], 0, 0, "object")
    tok = state.place_token(table["scene_id"], table["asset_id"], 0, 0, "token")
    # A fresh layer starts its own stack rather than inheriting the other's z.
    assert tok["z"] == obj["z"]


def test_unknown_layer_is_refused(table):
    with pytest.raises(ValueError, match="Unknown layer"):
        state.place_token(table["scene_id"], table["asset_id"], 0, 0, "ceiling")


def test_placing_a_missing_asset_is_refused(table):
    with pytest.raises(ValueError, match="asset"):
        state.place_token(table["scene_id"], 9999, 0, 0)


def test_placing_on_a_missing_scene_is_refused(table):
    with pytest.raises(ValueError, match="scene"):
        state.place_token(9999, table["asset_id"], 0, 0)


def test_positions_are_in_grid_units_not_pixels(table):
    """Changing the grid must not scatter what is already placed."""
    token = state.place_token(table["scene_id"], table["asset_id"], 4, 6)
    state.update_grid(table["map_id"], size_px=25)
    after = state.get_token(token["id"])
    assert (after["x"], after["y"]) == (4, 6)


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #

def test_scene_bounds_are_in_grid_squares(table):
    # The fixture map is 1400x1000 with the default grid guess.
    cols, rows = state.scene_bounds(table["scene_id"])
    grid = state.get_map(table["map_id"])["grid"]["size_px"]
    assert cols == pytest.approx(1400 / grid)
    assert rows == pytest.approx(1000 / grid)


def test_placement_far_off_the_map_is_pulled_back(table):
    """A stale zoom turns a screen coordinate into a wild grid coordinate."""
    cols, rows = state.scene_bounds(table["scene_id"])
    token = state.place_token(table["scene_id"], table["asset_id"], 670, 483)
    assert token["x"] <= cols
    assert token["y"] <= rows


def test_a_token_may_still_hang_over_an_edge(table):
    """Half a wagon poking off the road is legitimate."""
    token = state.place_token(table["scene_id"], table["asset_id"], -1, -0.5)
    assert token["x"] == -1        # grid_w is 2, so -2 is the limit
    assert token["y"] == -0.5


def test_dragging_far_off_the_map_is_pulled_back(table):
    cols, rows = state.scene_bounds(table["scene_id"])
    token = state.place_token(table["scene_id"], table["asset_id"], 1, 1)
    moved = state.update_token(token["id"], x=99999, y=-99999)
    assert moved["x"] <= cols
    assert moved["y"] >= -moved["grid_h"]


def test_bounds_are_none_for_a_scene_with_no_map(table):
    scene_id = state.create_scene(None, "Empty")
    assert state.scene_bounds(scene_id) is None
    # Clamping must be a no-op rather than an error when there is nothing to
    # clamp against.
    assert state.clamp_to_scene(scene_id, 500, 500, 1, 1) == (500, 500)


# --------------------------------------------------------------------------- #
# Mutation
# --------------------------------------------------------------------------- #

def test_move_resize_rotate_label(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    updated = state.update_token(
        token["id"], x=9.5, y=2.25, grid_w=3, grid_h=2,
        rotation=90, label="Bandit Captain",
    )
    assert (updated["x"], updated["y"]) == (9.5, 2.25)
    assert (updated["grid_w"], updated["grid_h"]) == (3, 2)
    assert updated["rotation"] == 90
    assert updated["label"] == "Bandit Captain"


@pytest.mark.parametrize("sent, expected", [
    (0, state.MIN_FOOTPRINT),
    (-5, state.MIN_FOOTPRINT),
    (9999, state.MAX_FOOTPRINT),
])
def test_footprint_is_clamped(table, sent, expected):
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    assert state.update_token(token["id"], grid_w=sent)["grid_w"] == expected


@pytest.mark.parametrize("sent, expected", [(450, 90), (-90, 270), (360, 0)])
def test_rotation_is_normalised(table, sent, expected):
    """Repeated rotation must not accumulate into unbounded values."""
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    assert state.update_token(token["id"], rotation=sent)["rotation"] == expected


def test_label_is_trimmed_and_bounded(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    assert state.update_token(token["id"], label="  Goblin  ")["label"] == "Goblin"
    assert len(state.update_token(token["id"], label="x" * 500)["label"]) <= 80
    assert state.update_token(token["id"], label="")["label"] is None


def test_updating_a_missing_token_returns_none(table):
    assert state.update_token(9999, x=1) is None


def test_empty_update_is_harmless(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 3, 3)
    assert state.update_token(token["id"])["x"] == 3


def test_delete(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    assert state.delete_token(token["id"]) is True
    assert state.get_token(token["id"]) is None
    assert state.delete_token(token["id"]) is False


def test_clear_tokens(table):
    for i in range(4):
        state.place_token(table["scene_id"], table["asset_id"], i, i)
    assert state.clear_tokens(table["scene_id"]) == 4
    assert state.list_tokens(table["scene_id"]) == []


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #

def test_tokens_are_listed_in_paint_order(table):
    creature = state.place_token(table["scene_id"], table["asset_id"], 0, 0, "token")
    backdrop = state.place_token(table["scene_id"], table["asset_id"], 0, 0, "map")
    furniture = state.place_token(table["scene_id"], table["asset_id"], 0, 0, "object")

    order = [t["id"] for t in state.list_tokens(table["scene_id"])]
    assert order == [backdrop["id"], furniture["id"], creature["id"]]


# --------------------------------------------------------------------------- #
# Visibility  (ADR-004)
# --------------------------------------------------------------------------- #

def test_hidden_tokens_are_absent_from_a_player_listing(table):
    visible = state.place_token(table["scene_id"], table["asset_id"], 1, 1)
    ambush = state.place_token(table["scene_id"], table["asset_id"], 8, 8)
    state.update_token(ambush["id"], hidden=True)

    gm_ids = [t["id"] for t in state.list_tokens(table["scene_id"], include_hidden=True)]
    player_ids = [t["id"] for t in state.list_tokens(table["scene_id"], include_hidden=False)]

    assert gm_ids == [visible["id"], ambush["id"]]
    assert player_ids == [visible["id"]]


def test_a_player_snapshot_leaks_nothing_about_a_hidden_token(table):
    ambush = state.place_token(table["scene_id"], table["asset_id"], 8, 8)
    state.update_token(ambush["id"], hidden=True, label="Assassin behind the bar")

    snapshot = state.snapshot(for_gm=False)
    serialised = repr(snapshot)

    assert snapshot["tokens"] == []
    # Not the position, not the label, not even the id.
    assert "Assassin" not in serialised
    assert not any(t["id"] == ambush["id"] for t in snapshot["tokens"])


def test_gm_snapshot_includes_hidden_tokens(table):
    ambush = state.place_token(table["scene_id"], table["asset_id"], 8, 8)
    state.update_token(ambush["id"], hidden=True)

    snapshot = state.snapshot(for_gm=True)
    assert [t["id"] for t in snapshot["tokens"]] == [ambush["id"]]
    assert snapshot["tokens"][0]["hidden"] is True


def test_revealing_puts_a_token_back_in_the_player_view(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 2, 2)
    state.update_token(token["id"], hidden=True)
    assert state.snapshot(for_gm=False)["tokens"] == []

    state.update_token(token["id"], hidden=False)
    assert [t["id"] for t in state.snapshot(for_gm=False)["tokens"]] == [token["id"]]


def test_locked_tokens_are_still_visible(table):
    """Locked stops it being dragged; it is not a visibility control."""
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    state.update_token(token["id"], locked=True)
    assert len(state.snapshot(for_gm=False)["tokens"]) == 1
    assert state.snapshot(for_gm=False)["tokens"][0]["locked"] is True
