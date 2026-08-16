"""Hit points, conditions, and what a player is allowed to know.

The rule worth testing is the one about numbers. A player who can read
``hp: 7, hp_max: 11`` out of a payload knows exactly how many hits the goblin
has left, which is the GM's information to give or withhold. They get a bar in
quarters instead -- coarse on purpose, because a precise fraction plus a known
maximum is the exact number again. See ADR-017.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, state, status


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A lit scene, an asset, and two players: one owns a token, one does not."""
    db_path = tmp_path / "status.db"
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
           VALUES ('Goblin', 'goblin.png', 'bundled', 1, 1)"""
    ).lastrowid
    anya = conn.execute(
        """INSERT INTO users (username, display_name, pw_hash, pw_salt,
                              pw_n, pw_r, pw_p, role)
           VALUES ('anya', 'Anya', 'x', 'y', 1, 1, 1, 'player')"""
    ).lastrowid
    brand = conn.execute(
        """INSERT INTO users (username, display_name, pw_hash, pw_salt,
                              pw_n, pw_r, pw_p, role)
           VALUES ('brand', 'Brand', 'x', 'y', 1, 1, 1, 'player')"""
    ).lastrowid
    conn.commit()

    yield {"scene_id": scene_id, "asset_id": asset_id, "anya": anya, "brand": brand}
    db.close()


def place(table, **changes):
    token = state.place_token(table["scene_id"], table["asset_id"], 1, 1, "token")
    return state.update_token(token["id"], **changes) if changes else token


# --------------------------------------------------------------------------- #
# The bar
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hp, hp_max, expected", [
    (11, 11, 4),
    (9, 11, 4),
    (6, 11, 3),
    (5, 11, 2),
    (3, 11, 2),
    (2, 11, 1),
    (1, 11, 1),
    (0, 11, 0),
    (-7, 11, 0),
])
def test_the_bar_is_quarters(hp, hp_max, expected):
    assert status.bar_steps(hp, hp_max) == expected


def test_one_hit_point_still_shows_a_sliver():
    """An empty bar reads as dead, and the difference matters to whoever is
    deciding whether to run."""
    assert status.bar_steps(1, 200) == 1


def test_nothing_tracked_is_no_bar():
    assert status.bar_steps(None, 11) is None
    assert status.bar_steps(7, None) is None
    assert status.bar_steps(7, 0) is None


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #

def test_conditions_round_trip_and_are_deduplicated():
    assert status.clean_conditions(["prone", "PRONE", "stunned"]) == "prone,stunned"
    assert status.clean_conditions("prone, stunned") == "prone,stunned"


def test_an_unknown_condition_is_dropped():
    """These are drawn as badges on a canvas; "whatever the client sent" is not
    something to render."""
    assert status.clean_conditions(["prone", "smug", "<script>"]) == "prone"


def test_the_condition_list_is_capped():
    everything = list(status.CONDITIONS)
    stored = status.clean_conditions(everything * 2)
    assert len(stored.split(",")) == status.MAX_CONDITIONS


def test_every_condition_has_a_distinct_badge():
    """"dead" and "deafened" would both be DE, which is a poor thing to be
    vague about."""
    shorts = [entry["short"] for entry in status.CONDITIONS.values()]
    assert len(set(shorts)) == len(shorts)
    assert all(len(short) == 2 for short in shorts)


def test_nothing_is_an_empty_list_not_a_crash():
    assert status.clean_conditions(None) == ""
    assert status.condition_list("") == []


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #

def test_hit_points_are_clamped_and_optional():
    assert status.clean_hp(None) is None
    assert status.clean_hp("") is None
    assert status.clean_hp(999_999) == status.MAX_HP
    assert status.clean_hp(-999_999) == status.MIN_HP
    # Negative current HP is a real state at many tables; a zero maximum is not.
    assert status.clean_hp(-3) == -3
    assert status.clean_hp_max(0) == 1
    assert status.clean_hp_max(-5) == 1


# --------------------------------------------------------------------------- #
# Who is told what
# --------------------------------------------------------------------------- #

def test_the_gm_sees_the_numbers(table):
    place(table, hp=7, hp_max=11, conditions=["prone"])
    token = state.snapshot(for_gm=True)["tokens"][0]

    assert (token["hp"], token["hp_max"]) == (7, 11)
    assert token["conditions"] == ["prone"]


def test_a_player_gets_a_bar_and_no_numbers(table):
    place(table, hp=7, hp_max=11)
    token = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert token["hp_bar"] == 3
    assert "hp" not in token
    assert "hp_max" not in token
    assert "7" not in repr(token)


def test_a_player_sees_their_own_characters_numbers(table):
    """It is their character. Hiding their own hit points would be absurd."""
    token = place(table, hp=7, hp_max=11)
    state.update_token(token["id"], **{})
    db.connect().execute(
        "UPDATE tokens SET owner_user_id = ? WHERE id = ?", (table["anya"], token["id"])
    )
    db.connect().commit()

    mine = state.snapshot(for_gm=False, viewer_id=table["anya"])["tokens"][0]
    theirs = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert (mine["hp"], mine["hp_max"]) == (7, 11)
    assert "hp" not in theirs
    assert theirs["hp_bar"] == 3


def test_a_gm_can_hide_the_bar_entirely(table):
    place(table, hp=7, hp_max=11, hp_public=False, conditions=["poisoned"])
    token = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert "hp" not in token
    assert "hp_bar" not in token
    # The condition is still public: a poisoned goblin looks poisoned.
    assert token["conditions"] == ["poisoned"]


def test_conditions_are_public(table):
    place(table, conditions=["prone", "frightened"])
    token = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert token["conditions"] == ["prone", "frightened"]


def test_a_token_with_no_health_says_nothing_about_health(table):
    place(table)
    gm = state.snapshot(for_gm=True)["tokens"][0]
    player = state.snapshot(for_gm=False, viewer_id=table["brand"])["tokens"][0]

    assert gm["hp"] is None and gm["hp_bar"] is None
    assert "hp_bar" not in player
    assert player["conditions"] == []


def test_the_vocabulary_travels_with_the_table(table):
    """One source of truth for the names, rather than a copy in the client."""
    for audience in (state.snapshot(for_gm=True), state.snapshot(for_gm=False)):
        assert audience["conditions"]["prone"]["short"] == "PR"


def test_health_survives_a_restart(table):
    token = place(table, hp=4, hp_max=9, conditions=["stunned"])
    db.close()
    db.migrate(config.DB_PATH)

    restored = state.get_token(token["id"])
    assert (restored["hp"], restored["hp_max"]) == (4, 9)
    assert restored["conditions"] == ["stunned"]
