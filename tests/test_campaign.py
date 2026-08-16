"""Campaign export and import.

Three things carry this. An export must be a *coherent* copy of a database that
is being written to, not a file copy that loses the last few minutes. It must
not carry live session tokens, which are credentials. And an import must not
extract wherever the archive says -- a zip can name `../../etc/passwd` as
easily as a filename. See ADR-019.
"""

import contextlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from ezvtt import campaign, config, db, handouts, media, state


@pytest.fixture
def campaign_dirs(tmp_path, monkeypatch):
    """A campaign with a map, an upload, a handout, an account, and a session."""
    for attribute, name in [
        ("DB_PATH", "data/ezvtt.db"), ("MAPS_DIR", "data/maps"),
        ("UPLOADS_DIR", "data/assets"), ("THUMBS_DIR", "data/thumbs"),
        ("FOG_DIR", "data/fog"), ("HANDOUTS_DIR", "data/handouts"),
    ]:
        monkeypatch.setattr(config, attribute, tmp_path / name)
    monkeypatch.setattr(
        config, "WRITABLE_DIRS",
        (config.MAPS_DIR, config.UPLOADS_DIR, config.THUMBS_DIR,
         config.FOG_DIR, config.HANDOUTS_DIR),
    )
    db.close()
    db.migrate(config.DB_PATH)

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (90, 70, 50)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    state.create_map(stored, "The Tavern")

    letter = io.BytesIO()
    Image.new("RGB", (200, 150), (220, 210, 180)).save(letter, "PNG")
    handouts.add(
        media.store_upload(letter.getvalue(), "Letter.png", config.HANDOUTS_DIR),
        "A letter",
    )

    conn = db.connect()
    conn.execute(
        """INSERT INTO users (username, display_name, pw_hash, pw_salt,
                              pw_n, pw_r, pw_p, role)
           VALUES ('gary', 'Gary', 'hash', 'salt', 1, 1, 1, 'admin')"""
    )
    conn.execute(
        """INSERT INTO sessions (token, user_id, expires_at)
           VALUES ('a-live-session-token', 1, datetime('now', '+30 days'))"""
    )
    db.set_setting("campaign_name", "The Sunless Citadel")
    conn.commit()

    yield tmp_path
    db.close()


def export(tmp_path) -> Path:
    target = tmp_path / "campaign.zip"
    campaign.export_to(target)
    return target


# --------------------------------------------------------------------------- #
# What travels
# --------------------------------------------------------------------------- #

def test_an_export_carries_the_database_and_the_media(campaign_dirs):
    archive = export(campaign_dirs)

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert "manifest.json" in names
    assert "ezvtt.db" in names
    assert any(n.startswith("media/maps/") for n in names)
    assert any(n.startswith("media/handouts/") for n in names)


def test_the_manifest_describes_the_campaign(campaign_dirs):
    archive = export(campaign_dirs)

    with zipfile.ZipFile(archive) as zf:
        info = json.loads(zf.read("manifest.json"))

    assert info["campaign_name"] == "The Sunless Citadel"
    assert info["archive_version"] == campaign.ARCHIVE_VERSION
    assert info["counts"]["maps"] == 1
    assert info["counts"]["handouts"] == 1


def test_live_sessions_do_not_travel(campaign_dirs):
    """An export carrying session tokens is a file that logs its holder in."""
    archive = export(campaign_dirs)
    extracted = campaign_dirs / "check.db"

    with zipfile.ZipFile(archive) as zf, extracted.open("wb") as sink:
        sink.write(zf.read("ezvtt.db"))

    with contextlib.closing(sqlite3.connect(extracted)) as copy:
        sessions = copy.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        users = copy.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    assert sessions == 0
    # Accounts stay: a restore that loses every login is not a restore.
    assert users == 1
    assert b"a-live-session-token" not in archive.read_bytes()


def test_derived_files_do_not_travel(campaign_dirs):
    """Thumbnails and fog composites rebuild themselves and are larger than
    what they rebuild from."""
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    (config.THUMBS_DIR / "something.png").write_bytes(b"derived")
    config.FOG_DIR.mkdir(parents=True, exist_ok=True)
    (config.FOG_DIR / "scene-1-v1.webp").write_bytes(b"derived")

    with zipfile.ZipFile(export(campaign_dirs)) as zf:
        names = zf.namelist()

    assert not any("thumbs" in n or "fog" in n for n in names)


def test_the_export_is_a_coherent_copy_not_a_file_copy(campaign_dirs):
    """VACUUM INTO, so the write-ahead log is folded in rather than left behind
    with the last few minutes of play in it."""
    state.create_map(
        media.store_upload(
            _png(), "Cellar.png", config.MAPS_DIR,
        ),
        "The Cellar",
    )
    archive = export(campaign_dirs)

    extracted = campaign_dirs / "coherent.db"
    with zipfile.ZipFile(archive) as zf:
        extracted.write_bytes(zf.read("ezvtt.db"))

    with contextlib.closing(sqlite3.connect(extracted)) as copy:
        names = [row[0] for row in copy.execute("SELECT name FROM maps ORDER BY id")]

    assert names == ["The Tavern", "The Cellar"]


def _png(width=64, height=48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, "PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Reading an archive
# --------------------------------------------------------------------------- #

def test_a_manifest_round_trips(campaign_dirs):
    archive = export(campaign_dirs)
    assert campaign.read_manifest(archive)["campaign_name"] == "The Sunless Citadel"


def test_something_that_is_not_a_zip_is_refused(campaign_dirs):
    bogus = campaign_dirs / "notes.txt"
    bogus.write_text("dear diary")

    with pytest.raises(campaign.CampaignError, match="not a zip"):
        campaign.read_manifest(bogus)


def test_a_zip_that_is_not_a_campaign_is_refused(campaign_dirs):
    bogus = campaign_dirs / "holiday-photos.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("beach.jpg", b"not a campaign")

    with pytest.raises(campaign.CampaignError, match="no manifest"):
        campaign.read_manifest(bogus)


def test_an_archive_from_a_newer_ezvtt_is_refused(campaign_dirs):
    """Better a clear refusal than a half-understood import."""
    future = campaign_dirs / "future.zip"
    with zipfile.ZipFile(future, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"archive_version": 99}))
        zf.writestr("ezvtt.db", b"")

    with pytest.raises(campaign.CampaignError, match="newer EzVTT"):
        campaign.read_manifest(future)


# --------------------------------------------------------------------------- #
# Importing
# --------------------------------------------------------------------------- #

def test_an_import_is_staged_rather_than_applied(campaign_dirs):
    """Swapping a SQLite file that open connections hold is how you corrupt it."""
    archive = export(campaign_dirs)
    before = config.DB_PATH.read_bytes()

    campaign.stage_import(archive)

    assert config.DB_PATH.read_bytes() == before
    assert campaign.pending() is not None
    assert campaign.incoming_path().is_file()


def test_a_staged_import_can_be_cancelled(campaign_dirs):
    campaign.stage_import(export(campaign_dirs))

    assert campaign.cancel_pending() is True
    assert campaign.pending() is None
    assert campaign.cancel_pending() is False


def test_applying_a_staged_import_keeps_the_old_database(campaign_dirs):
    """An import replaces an entire campaign, and "that was the wrong archive"
    needs a better answer than "restore from your own backup"."""
    archive = export(campaign_dirs)
    state.rename_map(1, "Renamed after the export")
    db.close()

    campaign.stage_import(archive)
    kept = campaign.apply_pending()

    assert kept and kept.startswith("ezvtt.db.replaced-")
    assert (config.DB_PATH.parent / kept).is_file()

    db.close()
    db.migrate(config.DB_PATH)
    assert state.get_map(1)["name"] == "The Tavern"


def test_applying_nothing_is_harmless(campaign_dirs):
    assert campaign.apply_pending() is None


def test_the_write_ahead_log_of_the_replaced_database_is_not_kept(campaign_dirs):
    """It belongs to the database being replaced. Left behind, it would apply a
    stranger's transactions to the arriving one."""
    archive = export(campaign_dirs)
    db.close()
    wal = config.DB_PATH.with_name(config.DB_PATH.name + "-wal")
    wal.write_bytes(b"stale transactions")

    campaign.stage_import(archive)
    campaign.apply_pending()

    assert not wal.is_file()


# --------------------------------------------------------------------------- #
# Hostile archives
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("member", [
    "../../../etc/passwd",
    "media/maps/../../../escape.png",
    "/absolute/path.png",
    "media/maps/subdir/deeper.png",
    "somewhere/else/entirely.png",
])
def test_an_archive_cannot_write_outside_the_campaign(campaign_dirs, member):
    """A zip names its own paths, and extracting one writes wherever it says."""
    hostile = campaign_dirs / "hostile.zip"
    with zipfile.ZipFile(hostile, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"archive_version": 1}))
        zf.writestr("ezvtt.db", b"")
        zf.writestr(member, b"pwned")

    campaign.stage_import(hostile)

    escaped = list(campaign_dirs.rglob("*escape*")) + list(campaign_dirs.rglob("*passwd*"))
    assert escaped == []
    assert not (config.MAPS_DIR / "subdir").exists()


def test_a_windows_style_traversal_is_refused_too(campaign_dirs):
    hostile = campaign_dirs / "hostile-win.zip"
    with zipfile.ZipFile(hostile, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"archive_version": 1}))
        zf.writestr("ezvtt.db", b"")
        zf.writestr("media\\\\maps\\\\..\\\\..\\\\escape.png", b"pwned")

    campaign.stage_import(hostile)

    assert list(campaign_dirs.rglob("*escape*")) == []


def test_media_from_a_well_formed_archive_does_land(campaign_dirs):
    """The refusals above must not be refusing everything."""
    archive = export(campaign_dirs)
    for path in config.MAPS_DIR.iterdir():
        path.unlink()

    campaign.stage_import(archive)

    assert list(config.MAPS_DIR.iterdir())
