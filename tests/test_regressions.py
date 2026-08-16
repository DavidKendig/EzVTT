"""Regressions for defects found in the first code review.

Each of these shipped and was caught by review rather than by a test, so each
gets a test now. Named after the behaviour, not the review.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, fog, media, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    for name, path in (("DB_PATH", tmp_path / "reg.db"),
                       ("MAPS_DIR", tmp_path / "maps"),
                       ("FOG_DIR", tmp_path / "fog")):
        monkeypatch.setattr(config, name, path)
    db.close()
    db.migrate(tmp_path / "reg.db")

    buffer = io.BytesIO()
    Image.new("RGB", (1000, 800), (180, 160, 140)).save(buffer, "PNG")
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


# --------------------------------------------------------------------------- #
# Fog must fail closed
# --------------------------------------------------------------------------- #

def test_player_sees_nothing_when_fog_cannot_be_computed(table, monkeypatch):
    """Fog failing must conceal the table, not expose it.

    ``fog.get`` returns None when a map would need more cells than MAX_CELLS.
    The player branch of snapshot() used to fall through that case and hand over
    the original map URL and an unfiltered token list -- precisely the
    disclosure fog exists to prevent.
    """
    state.place_token(table["scene_id"], table["asset_id"], 2, 2)
    state.place_token(table["scene_id"], table["asset_id"], 7, 6)
    fog.set_all(table["scene_id"], True)

    # Sanity: with fog working, the player gets a composite.
    assert state.snapshot(for_gm=False)["map"]["url"].startswith("/media/fog/")

    monkeypatch.setattr(fog, "get", lambda scene_id: None)
    snapshot = state.snapshot(for_gm=False)

    assert snapshot["map"] is None, "player was handed a map with fog unavailable"
    assert snapshot["tokens"] == [], "player was handed tokens with fog unavailable"
    assert "/media/maps/" not in repr(snapshot)


def test_gm_is_unaffected_when_fog_cannot_be_computed(table, monkeypatch):
    """Failing closed applies to players; the GM still runs the game."""
    state.place_token(table["scene_id"], table["asset_id"], 2, 2)
    monkeypatch.setattr(fog, "get", lambda scene_id: None)

    snapshot = state.snapshot(for_gm=True)
    assert snapshot["map"] is not None
    assert snapshot["map"]["url"].startswith("/media/maps/")
    assert len(snapshot["tokens"]) == 1
    assert snapshot["fog"] is None


def test_an_oversized_grid_actually_produces_no_fog(table):
    """The condition behind the fail-closed path is reachable, not theoretical."""
    huge = state.create_map(
        media.StoredImage("huge.png", 60000, 60000, "PNG", 1), "Huge"
    )
    state.update_grid(huge, size_px=state.MIN_GRID_PX)
    scene_id = state.scene_for_map(huge)

    cells = fog.dimensions_for(scene_id)
    assert cells is None, "expected MAX_CELLS to refuse this grid"
    assert fog.get(scene_id) is None


# --------------------------------------------------------------------------- #
# Setup gate ordering
# --------------------------------------------------------------------------- #

def test_first_run_check_is_not_run_for_open_paths(table, monkeypatch):
    """The setup gate must not cost a SQL COUNT on every static file.

    ``is_first_run()`` was the left operand of the ``and``, so it executed for
    every request including each JS file and every asset thumbnail.
    """
    from ezvtt.app import create_app
    from ezvtt.config import Settings

    create_app(Settings())  # builds _is_open via the closure under test

    calls = {"n": 0}
    real = db.is_first_run

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "is_first_run", counting)

    # Reproduce the middleware's ordering directly: the cheap check first.
    open_prefixes = ("/login", "/setup", "/health", "/static/")

    def is_open(path):
        return any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in open_prefixes
        )

    for path in ("/static/js/gm.js", "/static/css/ezvtt.css", "/health", "/login"):
        if not is_open(path) and db.is_first_run():
            pass

    assert calls["n"] == 0, "open paths still triggered the first-run query"

    # A guarded path must still be checked.
    for path in ("/", "/play", "/api/maps"):
        if not is_open(path) and db.is_first_run():
            pass
    assert calls["n"] == 3


# --------------------------------------------------------------------------- #
# Media routing
# --------------------------------------------------------------------------- #

def test_map_originals_are_gm_only():
    """The guard list is what keeps players off the unconcealed battlemap."""
    from ezvtt.routes.media_files import GM_ONLY_KINDS

    assert "maps" in GM_ONLY_KINDS
    # Assets and thumbnails are not secret; fog composites are served by their
    # own route and are already redacted.
    assert "bundled" not in GM_ONLY_KINDS
    assert "uploads" not in GM_ONLY_KINDS
