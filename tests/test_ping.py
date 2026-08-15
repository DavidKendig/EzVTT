"""Ping rules: who may ping, how often, and who is shown it.

The visibility rule is the one that matters. A ping is the GM's finger on the
map, and a player must not be shown one standing on ground they have not
revealed -- that would be pointing at exactly what the fog exists to keep from
them. See ADR-011 and ADR-014.
"""

import io
import math

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, ping, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    db_path = tmp_path / "ping.db"
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

    yield scene_id
    db.close()


# --------------------------------------------------------------------------- #
# The rate limit
# --------------------------------------------------------------------------- #

def test_a_first_ping_is_always_allowed():
    assert ping.allowed(None, now=100.0)


def test_a_second_ping_must_wait():
    """A stuck key must not strobe five screens at once."""
    assert not ping.allowed(100.0, now=100.5)
    assert ping.allowed(100.0, now=100.0 + ping.MIN_INTERVAL_SECONDS)
    assert ping.allowed(100.0, now=105.0)


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #

def test_a_position_round_trips():
    assert ping.clean_point(3, -4.5) == (3.0, -4.5)
    assert ping.clean_point("2", "7") == (2.0, 7.0)


@pytest.mark.parametrize("x, y", [
    (None, 1), (1, None), ("over there", 1), ({}, 2), (1, [3]),
])
def test_a_position_that_is_not_a_position_is_refused(x, y):
    with pytest.raises(ValueError, match="position"):
        ping.clean_point(x, y)


def test_nan_is_refused():
    """It survives JSON, compares false against every bound, and draws nowhere."""
    with pytest.raises(ValueError, match="position"):
        ping.clean_point(math.nan, 0)


@pytest.mark.parametrize("x, y", [
    (ping.MAX_COORDINATE + 1, 0), (0, -ping.MAX_COORDINATE - 1),
    (math.inf, 0), (0, -math.inf),
])
def test_somewhere_that_is_not_on_any_map_is_refused(x, y):
    with pytest.raises(ValueError, match="not a place"):
        ping.clean_point(x, y)


# --------------------------------------------------------------------------- #
# Who is shown it
# --------------------------------------------------------------------------- #

def test_a_gm_sees_every_ping(table):
    """Including the display window, which authenticates as one and is showing
    the real map with the fog drawn over it."""
    fog.set_all(table, False)
    assert ping.visible_to(4, 4, is_gm=True, fog_state=fog.get(table))


def test_a_player_is_shown_a_ping_on_ground_they_have_revealed(table):
    fog.set_all(table, False)
    fog.paint_rect(table, 0, 0, 3, 3, revealed=True)
    fog_state = fog.get(table)

    assert ping.visible_to(1.5, 1.5, is_gm=False, fog_state=fog_state)
    assert not ping.visible_to(12, 9, is_gm=False, fog_state=fog_state)


def test_a_player_is_shown_nothing_when_the_fog_could_not_be_built(table):
    """Fail closed, as the map and the tokens already do."""
    assert not ping.visible_to(1, 1, is_gm=False, fog_state=None)
    assert ping.visible_to(1, 1, is_gm=True, fog_state=None)


def test_a_ping_off_the_edge_of_the_map_is_not_shown_to_players(table):
    fog.set_all(table, True)
    assert not ping.visible_to(-3, -3, is_gm=False, fog_state=fog.get(table))
