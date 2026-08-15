"""Area-of-effect templates, and who is shown them.

A template is shared state, so it is validated rather than trusted: its colour
reaches a canvas fill style on every client, and its size decides how much work
each of them does. And it obeys the same disclosure rules as a token -- a
concealed template, or one drawn over map a player has not revealed, is absent
from their payload rather than filtered out of it later. See ADR-004, ADR-011.
"""

import io

import pytest
from PIL import Image

from ezvtt import aoe, config, db, fog, media, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A scene on the table, fully concealed as a new one is."""
    db_path = tmp_path / "aoe.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)

    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1000), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Tavern")
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    yield {"scene_id": scene_id, "map_id": map_id}
    db.close()


# --------------------------------------------------------------------------- #
# Placing
# --------------------------------------------------------------------------- #

def test_a_template_round_trips(table):
    placed = aoe.place(
        table["scene_id"], "cone", x=4, y=5, size=3, angle=90, color="#ff8800",
        label="Dragon breath",
    )

    assert placed["kind"] == "cone"
    assert (placed["x"], placed["y"], placed["size"], placed["angle"]) == (4, 5, 3, 90)
    assert placed["color"] == "#ff8800"
    assert placed["label"] == "Dragon breath"
    assert aoe.list_for(table["scene_id"]) == [placed]


@pytest.mark.parametrize("kind", ["circle", "cone", "line"])
def test_every_kind_is_accepted(table, kind):
    assert aoe.place(table["scene_id"], kind, 0, 0, 2)["kind"] == kind


def test_an_unknown_kind_is_refused(table):
    with pytest.raises(ValueError, match="Unknown template"):
        aoe.place(table["scene_id"], "hypercube", 0, 0, 2)


def test_placing_with_no_scene_is_refused(table):
    with pytest.raises(ValueError, match="map on the table"):
        aoe.place(9999, "circle", 0, 0, 2)


@pytest.mark.parametrize("sent, expected", [
    (0, aoe.MIN_SIZE), (-5, aoe.MIN_SIZE), (9999, aoe.MAX_SIZE),
])
def test_size_is_clamped(table, sent, expected):
    """A radius of zero draws nothing; one of ten thousand rasterises forever."""
    assert aoe.place(table["scene_id"], "circle", 0, 0, sent)["size"] == expected


def test_width_is_clamped(table):
    assert aoe.place(table["scene_id"], "line", 0, 0, 4, width=0)["width"] == aoe.MIN_WIDTH
    assert aoe.place(table["scene_id"], "line", 0, 0, 4, width=99)["width"] == aoe.MAX_WIDTH


def test_angle_is_normalised(table):
    """Repeated rotation must not accumulate into a huge number."""
    assert aoe.place(table["scene_id"], "cone", 0, 0, 3, angle=450)["angle"] == 90
    assert aoe.place(table["scene_id"], "cone", 0, 0, 3, angle=-90)["angle"] == 270


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "red", "#12", "#1234567", "', 'x", "#gggggg", "url(evil)",
])
def test_colour_must_be_hex(table, bad):
    """The value is interpolated into a canvas fill style on every client."""
    with pytest.raises(ValueError, match="hex"):
        aoe.place(table["scene_id"], "circle", 0, 0, 2, color=bad)


def test_a_blank_label_is_stored_as_nothing(table):
    assert aoe.place(table["scene_id"], "circle", 0, 0, 2, label="   ")["label"] is None


def test_the_count_is_capped(table):
    for _ in range(aoe.MAX_TEMPLATES):
        aoe.place(table["scene_id"], "circle", 0, 0, 2)

    with pytest.raises(ValueError, match="Clear some"):
        aoe.place(table["scene_id"], "circle", 0, 0, 2)


# --------------------------------------------------------------------------- #
# Changing and removing
# --------------------------------------------------------------------------- #

def test_update_changes_what_it_is_given_and_nothing_else(table):
    placed = aoe.place(table["scene_id"], "circle", 1, 1, 2)
    updated = aoe.update(placed["id"], x=6, size=4)

    assert (updated["x"], updated["size"]) == (6, 4)
    assert updated["y"] == placed["y"]
    assert updated["kind"] == "circle"


def test_an_empty_update_is_harmless(table):
    placed = aoe.place(table["scene_id"], "circle", 1, 1, 2)
    assert aoe.update(placed["id"]) == placed


def test_updating_a_missing_template_returns_none(table):
    assert aoe.update(9999, size=3) is None


def test_remove_and_clear(table):
    first = aoe.place(table["scene_id"], "circle", 1, 1, 2)
    aoe.place(table["scene_id"], "line", 2, 2, 5)

    assert aoe.remove(first["id"])
    assert len(aoe.list_for(table["scene_id"])) == 1

    assert aoe.clear(table["scene_id"]) == 1
    assert aoe.list_for(table["scene_id"]) == []


def test_removing_a_missing_template_reports_it(table):
    assert aoe.remove(9999) is False


def test_templates_cascade_with_their_scene(table):
    aoe.place(table["scene_id"], "circle", 1, 1, 2)
    state.delete_map(table["map_id"])

    assert db.connect().execute(
        "SELECT COUNT(*) AS n FROM aoe_templates"
    ).fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# What each audience is told
# --------------------------------------------------------------------------- #

def test_a_concealed_template_is_left_out_of_a_players_list(table):
    """A trap's blast radius prepped in advance is a spoiler shaped like a circle."""
    aoe.place(table["scene_id"], "circle", 1, 1, 2, label="Glyph of warding", hidden=True)
    aoe.place(table["scene_id"], "circle", 4, 4, 2, label="Fireball")

    visible = aoe.list_for(table["scene_id"], include_hidden=False)

    assert [t["label"] for t in visible] == ["Fireball"]
    assert len(aoe.list_for(table["scene_id"])) == 2


def test_a_template_in_the_dark_is_not_sent_to_players(table):
    """A circle drawn over unexplored map is a map of the unexplored part."""
    scene = table["scene_id"]
    lit = aoe.place(scene, "circle", 1, 1, 1)
    aoe.place(scene, "circle", 15, 12, 1, label="Somewhere they have not been")

    fog.set_all(scene, False)
    fog.paint_rect(scene, 0, 0, 3, 3, revealed=True)
    fog_state = fog.get(scene)

    survivors = aoe.visible_in_fog(aoe.list_for(scene), fog_state)

    assert [t["id"] for t in survivors] == [lit["id"]]


def test_nothing_survives_fog_that_could_not_be_built(table):
    """Fail closed, like every other player payload does. See ADR-011."""
    aoe.place(table["scene_id"], "circle", 1, 1, 2)
    assert aoe.visible_in_fog(aoe.list_for(table["scene_id"]), None) == []


def test_reach_covers_a_cone_pointed_any_way(table):
    """The box is deliberately generous: it must not clip a shape at any angle."""
    template = aoe.place(table["scene_id"], "cone", 10, 10, 4, angle=213)
    x, y, w, h = aoe.reach(template)

    assert x <= 10 - 4 and y <= 10 - 4
    assert x + w >= 10 + 4 and y + h >= 10 + 4


def test_the_snapshot_splits_the_two_audiences(table):
    scene = table["scene_id"]
    fog.set_all(scene, True)
    aoe.place(scene, "circle", 2, 2, 2, label="Fireball")
    aoe.place(scene, "circle", 3, 3, 2, label="The trap they cannot see", hidden=True)

    gm = state.snapshot(for_gm=True)
    player = state.snapshot(for_gm=False)

    assert len(gm["templates"]) == 2
    assert [t["label"] for t in player["templates"]] == ["Fireball"]
    assert "trap they cannot see" not in repr(player)


def test_the_snapshot_is_quiet_with_nothing_on_the_table(table):
    state.delete_map(table["map_id"])
    assert state.snapshot(for_gm=True)["templates"] == []
    assert state.snapshot(for_gm=False)["templates"] == []


def test_templates_belong_to_their_scene(table):
    """Switching scenes must not carry a fireball into the next encounter."""
    other = state.create_scene(table["map_id"], "Elsewhere")
    aoe.place(table["scene_id"], "circle", 1, 1, 2)

    state.activate_scene(other)
    assert state.snapshot(for_gm=True)["templates"] == []

    state.activate_scene(table["scene_id"])
    assert len(state.snapshot(for_gm=True)["templates"]) == 1
