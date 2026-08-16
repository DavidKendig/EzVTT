"""Handouts: the image a GM holds up to the table.

Two things are worth pinning. What is *showing* is one setting, not a scene
property, so switching maps mid-session does not put the letter away. And a
pointer left behind by a deleted handout must answer "nothing is showing"
rather than a broken image on five screens. See ADR-018.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, handouts, media, state


@pytest.fixture
def library(tmp_path, monkeypatch):
    db_path = tmp_path / "handouts.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    monkeypatch.setattr(config, "HANDOUTS_DIR", tmp_path / "handouts")
    db.close()
    db.migrate(db_path)
    yield tmp_path
    db.close()


def add(title="A letter", width=800, height=600):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 190, 160)).save(buffer, "PNG")
    stored = media.store_upload(
        buffer.getvalue(), f"{title}.png", config.HANDOUTS_DIR
    )
    return handouts.add(stored, title)


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #

def test_a_handout_round_trips(library):
    handout = add("The traitor's letter", 900, 500)

    assert handout["title"] == "The traitor's letter"
    assert handout["url"].startswith("/media/handouts/")
    assert handout["thumb_url"].startswith("/media/thumbs/handouts/")
    assert (handout["width_px"], handout["height_px"]) == (900, 500)
    assert handouts.listing() == [handout]


def test_handouts_are_listed_newest_first(library):
    first = add("First")
    second = add("Second")
    assert [h["id"] for h in handouts.listing()] == [second["id"], first["id"]]


def test_rename(library):
    handout = add("Untitled")
    assert handouts.rename(handout["id"], "The sigil")
    assert handouts.get(handout["id"])["title"] == "The sigil"


def test_rename_rejects_an_empty_title(library):
    handout = add()
    with pytest.raises(ValueError):
        handouts.rename(handout["id"], "   ")


def test_renaming_a_missing_handout_reports_it(library):
    assert handouts.rename(9999, "Anything") is False


def test_delete_returns_the_file_to_remove(library):
    handout = add()
    filename = handouts.remove(handout["id"])

    assert filename and filename.endswith(".png")
    assert handouts.listing() == []


def test_deleting_a_missing_handout_reports_it(library):
    assert handouts.remove(9999) is None


# --------------------------------------------------------------------------- #
# Holding one up
# --------------------------------------------------------------------------- #

def test_nothing_is_showing_to_begin_with(library):
    assert handouts.showing() is None


def test_show_and_hide(library):
    handout = add("A portrait")

    assert handouts.show(handout["id"])["title"] == "A portrait"
    assert handouts.showing()["id"] == handout["id"]

    handouts.hide()
    assert handouts.showing() is None


def test_only_one_is_up_at_a_time(library):
    """Holding something up means holding *one* thing up."""
    first = add("First")
    second = add("Second")

    handouts.show(first["id"])
    handouts.show(second["id"])

    assert handouts.showing()["id"] == second["id"]


def test_showing_something_that_does_not_exist_is_refused(library):
    with pytest.raises(ValueError, match="no longer exists"):
        handouts.show(9999)


def test_deleting_what_is_on_screen_takes_it_off_screen(library):
    """Otherwise every client asks for an image that is not there any more."""
    handout = add()
    handouts.show(handout["id"])

    handouts.remove(handout["id"])

    assert handouts.showing() is None


def test_a_dangling_pointer_reads_as_nothing_showing(library):
    """Belt and braces: a row deleted around the pointer must not break a page."""
    handout = add()
    handouts.show(handout["id"])
    db.connect().execute("DELETE FROM handouts WHERE id = ?", (handout["id"],))
    db.connect().commit()

    assert handouts.showing() is None


# --------------------------------------------------------------------------- #
# What each audience is told
# --------------------------------------------------------------------------- #

def test_both_audiences_are_shown_the_same_handout(library):
    """The one thing here where everybody sees exactly the same picture."""
    handout = add("The sigil")
    handouts.show(handout["id"])

    gm = state.snapshot(for_gm=True)["handout"]
    player = state.snapshot(for_gm=False)["handout"]

    assert gm == player
    assert player["title"] == "The sigil"


def test_the_snapshot_says_nothing_when_nothing_is_up(library):
    assert state.snapshot(for_gm=False)["handout"] is None


def test_holding_something_up_outlasts_a_scene_switch(library):
    """It is what the table is looking at, not part of the encounter."""
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Tavern")
    first = state.scene_for_map(map_id)
    second = state.create_scene(map_id, "Elsewhere")

    handout = add("A letter")
    handouts.show(handout["id"])

    state.activate_scene(first)
    assert state.snapshot(for_gm=True)["handout"]["id"] == handout["id"]

    state.activate_scene(second)
    assert state.snapshot(for_gm=True)["handout"]["id"] == handout["id"]


def test_handouts_are_served_from_a_root_players_may_read(library):
    """Unlike MAPS_DIR, which is GM-only -- showing one to the table is the
    entire point of a handout. See ADR-011 for the map rule this differs from."""
    from ezvtt.routes import media_files

    assert media.media_root("handouts") == config.HANDOUTS_DIR
    assert "handouts" not in media_files.GM_ONLY_KINDS
    assert "maps" in media_files.GM_ONLY_KINDS
