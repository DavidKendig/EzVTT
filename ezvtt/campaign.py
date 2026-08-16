"""Campaign export and import: the whole table in one file.

Copying ``data/`` has always been the backup. This is the same thing as one
archive a GM can email themselves, without the parts that regenerate and
without the parts that should not travel.

**What is in it**: the database, the battlemaps, the uploaded art, the handouts.

**What is not**: thumbnails and fog composites, which are rebuilt on demand and
would double the size; the bundled Cartos artwork, which is 537 MB under its own
licence and is installed by a script (ADR-003, ADR-005); and **live sessions**,
which are credentials -- an export that carried them would be a file that logs
its holder in. Accounts themselves stay, because a restore that loses everyone's
login is not a restore. See ADR-019.

**Importing replaces everything, and cannot be done under a running server.**
The archive is staged beside the database and swapped in on the next start,
with the old database kept. Overwriting a SQLite file that open connections are
holding is a good way to produce a corrupt one.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

from . import __version__, config, db

log = logging.getLogger("ezvtt.campaign")

# Bumped when the archive layout changes in a way an older EzVTT could not
# read. The schema inside is versioned separately, by the migration runner.
ARCHIVE_VERSION = 1

MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "ezvtt.db"

# Directory in the archive -> where it lands. Thumbnails and fog composites are
# absent on purpose: both are derived, and both are larger than what they
# derive from.
MEDIA = {
    "media/maps": "MAPS_DIR",
    "media/assets": "UPLOADS_DIR",
    "media/handouts": "HANDOUTS_DIR",
}

# A staged database waiting for a restart, and where the outgoing one is kept.
INCOMING_SUFFIX = ".incoming"
REPLACED_SUFFIX = ".replaced"

# An archive whose paths escape the extraction root, or which unpacks to
# absurdity, is refused rather than trusted. See ADR-019.
MAX_UNPACKED_BYTES = 8 * 1024 * 1024 * 1024


class CampaignError(Exception):
    """Something is wrong with the archive. The message is user-facing."""


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _snapshot_database(target: Path) -> None:
    """Copy the database consistently, then strip what must not travel.

    ``VACUUM INTO`` takes a coherent copy while the server is running, which a
    file copy does not: the live database has a write-ahead log beside it, and
    copying the one without the other produces a file that is missing the last
    few minutes of play.
    """
    conn = db.connect()
    conn.execute("VACUUM INTO ?", (str(target),))

    # closing(), not `with sqlite3.connect(...)`: that context manager commits
    # the transaction and leaves the connection **open**, which on Windows
    # means the file cannot be deleted afterwards. Export would then fail on
    # the platform most of this program's users run it on.
    copy = sqlite3.connect(target)
    try:
        # Session tokens are live credentials. An export carrying them is a
        # file that logs its holder in as whoever was signed in when it was
        # made. Accounts stay: a restore that loses every login is not one.
        copy.execute("DELETE FROM sessions")
        copy.commit()
        copy.execute("VACUUM")
    finally:
        copy.close()


def manifest() -> dict[str, Any]:
    conn = db.connect()

    def count(table: str) -> int:
        # Table names are literals from this function, never from input.
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608

    return {
        "archive_version": ARCHIVE_VERSION,
        "ezvtt_version": __version__,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "campaign_name": db.get_setting("campaign_name", "A New Campaign"),
        "counts": {
            table: count(table)
            for table in ("users", "maps", "scenes", "tokens", "handouts", "notes")
        },
    }


def export_to(target: Path) -> dict[str, Any]:
    """Write a campaign archive. Returns the manifest that went into it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    info = manifest()

    staging = target.with_suffix(".part")
    database = target.with_name(target.name + ".db.tmp")
    staging.unlink(missing_ok=True)
    database.unlink(missing_ok=True)

    try:
        _snapshot_database(database)

        with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(info, indent=2))
            archive.write(database, DATABASE_NAME)

            for prefix, attribute in MEDIA.items():
                root: Path = getattr(config, attribute)
                if not root.is_dir():
                    continue
                for path in sorted(root.iterdir()):
                    if path.is_file():
                        archive.write(path, f"{prefix}/{path.name}")

        staging.replace(target)
    finally:
        database.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)

    log.info("Exported campaign to %s (%d bytes)", target.name, target.stat().st_size)
    return info


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def read_manifest(archive_path: Path) -> dict[str, Any]:
    """The manifest from an archive, or raise CampaignError explaining why not."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            if MANIFEST_NAME not in names or DATABASE_NAME not in names:
                raise CampaignError(
                    "That does not look like an EzVTT campaign: no manifest inside."
                )
            info = json.loads(archive.read(MANIFEST_NAME))
    except zipfile.BadZipFile as exc:
        raise CampaignError("That file is not a zip archive.") from exc
    except (json.JSONDecodeError, KeyError) as exc:
        raise CampaignError("The manifest in that archive is unreadable.") from exc

    version = info.get("archive_version")
    if not isinstance(version, int) or version > ARCHIVE_VERSION:
        raise CampaignError(
            f"That archive was made by a newer EzVTT (format {version}). "
            f"Update EzVTT and try again."
        )
    return info


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Entries that are safe to extract, refusing anything that escapes.

    A zip may name ``../../etc/passwd`` or an absolute path just as easily as a
    filename, and extracting one writes wherever it says. Every member is
    checked against the roots this archive is allowed to touch.
    """
    allowed = tuple(f"{prefix}/" for prefix in MEDIA)
    total = 0
    members = []

    for member in archive.infolist():
        if member.is_dir():
            continue
        name = member.filename.replace("\\", "/")
        if name in (MANIFEST_NAME, DATABASE_NAME):
            total += member.file_size
            members.append(member)
            continue

        if not name.startswith(allowed):
            log.warning("Refusing archive member outside the campaign: %s", name)
            continue
        # Belt and braces on top of the prefix check: no traversal, no absolute
        # paths, no drive letters.
        tail = name.split("/", 2)[-1]
        if not tail or "/" in tail or ".." in tail or ":" in tail:
            log.warning("Refusing suspicious archive member: %s", name)
            continue

        total += member.file_size
        if total > MAX_UNPACKED_BYTES:
            raise CampaignError("That archive unpacks to more than EzVTT will accept.")
        members.append(member)

    return members


def incoming_path() -> Path:
    return config.DB_PATH.with_name(config.DB_PATH.name + INCOMING_SUFFIX)


def stage_import(archive_path: Path) -> dict[str, Any]:
    """Unpack a campaign, ready for the next start. Returns its manifest.

    Media lands immediately -- files under new names cannot clash with what is
    already there -- and the database is staged. Swapping a SQLite file that
    open connections are holding is how you get a corrupt one.
    """
    info = read_manifest(archive_path)
    config.ensure_directories()

    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)

        for member in members:
            name = member.filename.replace("\\", "/")
            if name == MANIFEST_NAME:
                continue

            if name == DATABASE_NAME:
                with archive.open(member) as source, incoming_path().open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                continue

            prefix, filename = name.rsplit("/", 1)
            root: Path = getattr(config, MEDIA[prefix])
            root.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, (root / filename).open("wb") as sink:
                shutil.copyfileobj(source, sink)

    log.info("Staged campaign %r for the next start", info.get("campaign_name"))
    return info


def pending() -> dict[str, Any] | None:
    """The staged import waiting for a restart, if there is one."""
    path = incoming_path()
    if not path.is_file():
        return None
    return {"path": str(path), "bytes": path.stat().st_size}


def cancel_pending() -> bool:
    path = incoming_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def apply_pending() -> str | None:
    """Swap in a staged campaign. Called at startup, before any migration.

    The outgoing database is kept beside the new one rather than deleted. An
    import replaces an entire campaign, and "it turned out to be the wrong
    archive" needs an answer better than "restore from your own backup".
    """
    staged = incoming_path()
    if not staged.is_file():
        return None

    if config.DB_PATH.is_file():
        kept = config.DB_PATH.with_name(
            f"{config.DB_PATH.name}{REPLACED_SUFFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        config.DB_PATH.replace(kept)
    else:
        kept = None

    # The write-ahead log belongs to the database being replaced, not to the
    # one arriving; leaving it would apply a stranger's transactions.
    for suffix in ("-wal", "-shm"):
        config.DB_PATH.with_name(config.DB_PATH.name + suffix).unlink(missing_ok=True)

    staged.replace(config.DB_PATH)
    log.warning(
        "Imported campaign applied. The previous database is kept as %s",
        kept.name if kept else "(there was none)",
    )
    return kept.name if kept else ""
