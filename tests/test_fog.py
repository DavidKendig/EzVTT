"""Fog of war: the mask, the brush, and what each audience is told.

The snapshot tests are the ones that matter. Fog is an access-control boundary,
not an overlay: a player's payload must not contain the concealed map, the mask
that describes it, or the tokens standing in it. See ADR-004 and ADR-011.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, state

# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cells", [
    [],
    [False],
    [True],
    [False] * 50,
    [True] * 50,
    [True, False, True, False, True],
    [False] * 20 + [True] * 30 + [False] * 10,
])
def test_rle_round_trip(cells):
    assert fog.decode(fog.encode(cells), len(cells)) == cells


def test_rle_actually_compresses():
    """A concealed 8000-cell map must not cost 8000 bytes in every payload."""
    encoded = fog.encode([False] * 8000)
    assert len(encoded) < 20


def test_decode_pads_a_short_mask():
    assert fog.decode("1:3", 10) == [True] * 3 + [False] * 7


def test_decode_truncates_a_long_mask():
    assert fog.decode("1:100", 4) == [True] * 4


@pytest.mark.parametrize("junk", ["nonsense", "1:", ":5", "1:abc", "2:3,,", "1:-4"])
def test_decode_survives_corrupt_input(junk):
    """Losing fog beats refusing to open the scene."""
    result = fog.decode(junk, 6)
    assert len(result) == 6
    assert all(isinstance(c, bool) for c in result)


# --------------------------------------------------------------------------- #
# The mask
# --------------------------------------------------------------------------- #

@pytest.fixture
def table(tmp_path, monkeypatch):
    for name, path in (("DB_PATH", tmp_path / "fog.db"),
                       ("MAPS_DIR", tmp_path / "maps"),
                       ("FOG_DIR", tmp_path / "fog")):
        monkeypatch.setattr(config, name, path)
    db.close()
    db.migrate(tmp_path / "fog.db")

    buffer = io.BytesIO()
    # 1000x800 at a 100px grid gives a tidy 10x8 cell mask.
    Image.new("RGB", (1000, 800), (200, 180, 160)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Tavern")
    state.update_grid(map_id, size_px=100, offset_x=0, offset_y=0)
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    conn = db.connect()
    cursor = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h)
           VALUES ('Barrel', 'barrel.png', 'bundled', 1, 1)"""
    )
    conn.commit()

    yield {"scene_id": scene_id, "map_id": map_id, "asset_id": cursor.lastrowid}
    db.close()


def test_a_new_scene_starts_fully_concealed(table):
    """A map that arrives visible defeats the point of prepping it."""
    state_ = fog.get(table["scene_id"])
    assert (state_["cols"], state_["rows"]) == (10, 8)
    assert not any(state_["cells"])


def test_reveal_and_hide_all(table):
    assert all(fog.set_all(table["scene_id"], True)["cells"])
    assert not any(fog.set_all(table["scene_id"], False)["cells"])


def test_rectangle_reveals_only_its_cells(table):
    after = fog.paint_rect(table["scene_id"], 0, 0, 2, 1, revealed=True)
    assert fog.is_revealed(after, 0, 0)
    assert fog.is_revealed(after, 2, 1)
    assert not fog.is_revealed(after, 3, 0)
    assert not fog.is_revealed(after, 0, 2)
    assert sum(after["cells"]) == 6      # 3 wide by 2 tall


def test_brush_is_round_not_square(table):
    after = fog.paint(table["scene_id"], 5, 4, radius=2, revealed=True)
    # Straight out from the centre is inside the circle...
    assert fog.is_revealed(after, 5, 2)
    # ...but the corner of the bounding square is not.
    assert not fog.is_revealed(after, 3, 2)


def test_brush_clamps_at_the_edges(table):
    after = fog.paint(table["scene_id"], 0, 0, radius=5, revealed=True)
    assert fog.is_revealed(after, 0, 0)
    assert len(after["cells"]) == 80        # nothing spilled outside the grid


def test_painting_persists(table):
    fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)
    db.close()
    reloaded = fog.get(table["scene_id"])
    assert fog.is_revealed(reloaded, 0, 0)
    assert not fog.is_revealed(reloaded, 5, 5)


def test_version_advances_on_change_only(table):
    first = fog.get(table["scene_id"])["version"]
    after = fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)
    assert after["version"] > first
    # Painting the same cells again changes nothing, so the cached composite
    # must not be invalidated for no reason.
    again = fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)
    assert again["version"] == after["version"]


def test_mask_is_remapped_when_the_grid_changes(table):
    fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)
    state.update_grid(table["map_id"], size_px=50)      # 20x16 cells now

    after = fog.get(table["scene_id"])
    assert (after["cols"], after["rows"]) == (20, 16)
    # An evening's revealing survives a grid tweak, positioned as it was.
    assert fog.is_revealed(after, 0, 0)


def test_area_revealed_is_any_not_all(table):
    """A wagon straddling a lit doorway should be visible."""
    after = fog.paint_rect(table["scene_id"], 0, 0, 0, 0, revealed=True)
    assert fog.area_revealed(after, 0, 0, 2, 2)      # overlaps the lit cell
    assert not fog.area_revealed(after, 5, 5, 2, 2)  # entirely in the dark


# --------------------------------------------------------------------------- #
# What each audience is told
# --------------------------------------------------------------------------- #

def test_gm_gets_the_mask_and_the_real_map(table):
    snapshot = state.snapshot(for_gm=True)
    assert snapshot["fog"] is not None
    assert len(snapshot["fog"]["cells"]) == 80
    assert snapshot["map"]["url"].startswith("/media/maps/")


def test_player_gets_no_mask_and_a_composite(table):
    snapshot = state.snapshot(for_gm=False)
    assert snapshot.get("fog") is None
    assert snapshot["map"]["url"].startswith("/media/fog/")


def test_player_payload_never_names_the_map_file(table):
    """The filename alone would be enough to fetch the untouched image."""
    filename = state.get_map(table["map_id"])["url"].rsplit("/", 1)[-1]
    assert filename not in repr(state.snapshot(for_gm=False))


def test_tokens_in_the_dark_are_omitted_from_player_payloads(table):
    lit = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    dark = state.place_token(table["scene_id"], table["asset_id"], 8, 6)
    fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)

    gm_ids = [t["id"] for t in state.snapshot(for_gm=True)["tokens"]]
    player_ids = [t["id"] for t in state.snapshot(for_gm=False)["tokens"]]

    assert gm_ids == [lit["id"], dark["id"]]
    assert player_ids == [lit["id"]]


def test_revealing_ground_reveals_its_token(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 8, 6)
    assert state.snapshot(for_gm=False)["tokens"] == []

    fog.paint_rect(table["scene_id"], 8, 6, 8, 6, revealed=True)
    assert [t["id"] for t in state.snapshot(for_gm=False)["tokens"]] == [token["id"]]


def test_a_hidden_token_stays_hidden_even_in_the_light(table):
    """The two controls are independent, and the stricter one wins."""
    token = state.place_token(table["scene_id"], table["asset_id"], 0, 0)
    state.update_token(token["id"], hidden=True)
    fog.set_all(table["scene_id"], True)
    assert state.snapshot(for_gm=False)["tokens"] == []


# --------------------------------------------------------------------------- #
# The composite
# --------------------------------------------------------------------------- #

def test_a_concealed_composite_is_entirely_black(table):
    fog.set_all(table["scene_id"], False)
    path = fog.build_composite(table["scene_id"])
    assert path is not None and path.is_file()

    with Image.open(path) as image:
        assert image.convert("L").getextrema() == (0, 0)


def test_a_revealed_composite_shows_the_map(table):
    fog.set_all(table["scene_id"], True)
    with Image.open(fog.build_composite(table["scene_id"])) as image:
        assert image.convert("L").getextrema()[1] > 0


def test_a_partial_composite_is_black_only_where_concealed(table):
    fog.set_all(table["scene_id"], False)
    fog.paint_rect(table["scene_id"], 0, 0, 4, 3, revealed=True)

    with Image.open(fog.build_composite(table["scene_id"])) as image:
        grey = image.convert("L")
        w, h = grey.size
        assert grey.crop((0, 0, w // 4, h // 4)).getextrema()[1] > 0   # revealed
        # Clear of the ~16px boundary hairline; see the tolerance test below.
        assert grey.crop((w // 2 + 20, h // 2 + 20, w, h)).getextrema() == (0, 0)


def test_the_concealed_boundary_leaks_at_most_a_hairline(table):
    """Lossy WebP rings by a unit or two along a hard black edge.

    Lossy is kept deliberately: on a real battlemap the composite is six times
    smaller and three times faster to build than lossless, which matters for an
    image rebuilt on every brush stroke. The cost is bounded here rather than
    assumed -- measured at luminance 1 within roughly 16 pixels of the edge and
    exactly 0 beyond, alongside pixels the player is already allowed to see.
    """
    fog.set_all(table["scene_id"], False)
    fog.paint_rect(table["scene_id"], 0, 0, 4, 3, revealed=True)

    with Image.open(fog.build_composite(table["scene_id"])) as image:
        grey = image.convert("L")
        w, h = grey.size
        # The whole concealed half, boundary included.
        worst = grey.crop((w // 2, h // 2, w, h)).getextrema()[1]

    assert worst <= 4, f"concealed area reached luminance {worst}"


def test_composites_are_cached_per_version(table):
    first = fog.build_composite(table["scene_id"])
    assert fog.build_composite(table["scene_id"]) == first

    fog.paint_rect(table["scene_id"], 0, 0, 1, 1, revealed=True)
    assert fog.build_composite(table["scene_id"]) != first


def test_old_composites_are_pruned(table):
    """A long session brushes fog hundreds of times."""
    for i in range(6):
        fog.paint_rect(table["scene_id"], i, 0, i, 0, revealed=True)
        fog.build_composite(table["scene_id"])

    remaining = list(config.FOG_DIR.glob("scene-*-v*.webp"))
    assert len(remaining) <= 2, [p.name for p in remaining]


def test_composite_is_bounded_in_size(table, monkeypatch):
    """Full resolution is wasted on a browser canvas and too slow to rebuild."""
    buffer = io.BytesIO()
    Image.new("RGB", (9000, 300), (10, 20, 30)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Huge.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Huge")
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    with Image.open(fog.build_composite(scene_id)) as image:
        assert max(image.size) <= fog.COMPOSITE_MAX_EDGE


def test_no_composite_without_a_map(table):
    scene_id = state.create_scene(None, "Empty")
    assert fog.get(scene_id) is None
    assert fog.build_composite(scene_id) is None
