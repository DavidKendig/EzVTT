"""Grid clamping, scene activation, and what each audience is told.

The snapshot tests matter beyond Phase 1: fog of war in Phase 4 depends on a
player's snapshot being built without data they may not see, rather than being
filtered on the client. See ADR-004.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, media, state


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A fresh database and media root for each test."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)
    yield db_path
    db.close()


def make_map(name="Tavern", width=1400, height=1000) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (80, 60, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), f"{name}.png", config.MAPS_DIR)
    return state.create_map(stored, name)


# --------------------------------------------------------------------------- #
# Maps
# --------------------------------------------------------------------------- #

def test_new_map_gets_a_usable_starting_grid(board):
    """A sensible guess means the GM nudges the slider rather than hunts."""
    map_id = make_map(width=1400, height=1000)
    grid = state.get_map(map_id)["grid"]
    assert state.MIN_GRID_PX <= grid["size_px"] <= state.MAX_GRID_PX
    # 1000px tall over 30 squares is about 33px; anything in this range is a
    # believable battlemap square rather than a wild guess.
    assert 20 <= grid["size_px"] <= 60


def test_rename_and_delete(board):
    map_id = make_map()
    assert state.rename_map(map_id, "Goblin Ambush")
    assert state.get_map(map_id)["name"] == "Goblin Ambush"

    filename = state.delete_map(map_id)
    assert filename
    assert state.get_map(map_id) is None


def test_rename_rejects_an_empty_name(board):
    map_id = make_map()
    with pytest.raises(ValueError):
        state.rename_map(map_id, "   ")


def test_deleting_a_missing_map_returns_none(board):
    assert state.delete_map(9999) is None


# --------------------------------------------------------------------------- #
# Grid
# --------------------------------------------------------------------------- #

def test_grid_updates_round_trip(board):
    map_id = make_map()
    updated = state.update_grid(
        map_id, size_px=42.5, offset_x=13, offset_y=-8,
        opacity=0.66, color="#ff8800", visible=False,
    )
    grid = updated["grid"]
    assert grid["size_px"] == 42.5
    assert grid["offset_x"] == 13
    assert grid["offset_y"] == -8
    assert grid["opacity"] == 0.66
    assert grid["color"] == "#ff8800"
    assert grid["visible"] is False


@pytest.mark.parametrize("sent, expected", [
    (0, state.MIN_GRID_PX),
    (-50, state.MIN_GRID_PX),
    (99999, state.MAX_GRID_PX),
])
def test_grid_size_is_clamped(board, sent, expected):
    """A zero or negative grid would divide by zero in every conversion."""
    map_id = make_map()
    assert state.update_grid(map_id, size_px=sent)["grid"]["size_px"] == expected


@pytest.mark.parametrize("sent, expected", [(-3, 0.0), (5, 1.0)])
def test_opacity_is_clamped(board, sent, expected):
    map_id = make_map()
    assert state.update_grid(map_id, opacity=sent)["grid"]["opacity"] == expected


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "red", "#12", "#1234567",
    "', 'x", "#gggggg", "url(evil)",
])
def test_grid_colour_must_be_hex(board, bad):
    """The value is interpolated into a canvas stroke style on the client."""
    map_id = make_map()
    with pytest.raises(ValueError, match="hex"):
        state.update_grid(map_id, color=bad)


@pytest.mark.parametrize("good", ["#000", "#fff", "#FF8800", "#123abc"])
def test_valid_hex_colours_are_accepted(board, good):
    map_id = make_map()
    assert state.update_grid(map_id, color=good)["grid"]["color"] == good


def test_updating_a_missing_map_returns_none(board):
    assert state.update_grid(9999, size_px=50) is None


def test_empty_update_is_harmless(board):
    map_id = make_map()
    before = state.get_map(map_id)["grid"]
    assert state.update_grid(map_id)["grid"] == before


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #

def test_activating_a_scene_deactivates_the_previous_one(board):
    first = make_map("First")
    second = make_map("Second")

    state.activate_scene(state.scene_for_map(first))
    assert state.active_scene()["map_id"] == first

    state.activate_scene(state.scene_for_map(second))
    assert state.active_scene()["map_id"] == second

    # The partial unique index permits exactly one; confirm the count agrees.
    row = db.connect().execute(
        "SELECT COUNT(*) AS n FROM scenes WHERE is_active = 1"
    ).fetchone()
    assert row["n"] == 1


def test_scene_for_map_is_stable(board):
    map_id = make_map()
    assert state.scene_for_map(map_id) == state.scene_for_map(map_id)


def test_activating_a_missing_scene_fails_cleanly(board):
    assert state.activate_scene(9999) is False


def test_deleting_the_active_map_clears_the_table(board):
    map_id = make_map()
    state.activate_scene(state.scene_for_map(map_id))
    state.delete_map(map_id)
    # Scenes cascade from the map, so nothing is left active or dangling.
    assert state.active_scene() is None


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #

def test_gm_snapshot_includes_the_library(board):
    make_map("A")
    make_map("B")
    snapshot = state.snapshot(for_gm=True)
    assert len(snapshot["library"]) == 2


def test_player_snapshot_omits_the_library(board):
    """Players have no business seeing maps that are not on the table."""
    make_map("Secret Boss Arena")
    snapshot = state.snapshot(for_gm=False)
    assert "library" not in snapshot
    assert "Secret Boss Arena" not in repr(snapshot)


def test_snapshot_with_nothing_on_the_table(board):
    snapshot = state.snapshot(for_gm=True)
    assert snapshot["map"] is None
    assert snapshot["scene"] is None


def test_snapshot_carries_the_active_map(board):
    map_id = make_map("Tavern")
    state.activate_scene(state.scene_for_map(map_id))

    gm = state.snapshot(for_gm=True)
    assert gm["map"]["name"] == "Tavern"
    assert gm["map"]["url"].startswith("/media/maps/")

    # A player is pointed at a composite instead; fog owns that rule and
    # tests/test_fog.py covers it.
    player = state.snapshot(for_gm=False)
    assert player["map"]["name"] == "Tavern"
    assert player["map"]["url"].startswith("/media/fog/")
