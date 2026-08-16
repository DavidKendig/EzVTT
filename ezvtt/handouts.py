"""Handouts: the image a GM holds up to the table.

A letter, a portrait, the symbol carved into the door. Kept in a library
because the same handout comes back out three sessions later, and shown one at
a time because that is what holding something up means.

**Showing is not a scene property.** It is what the table is looking at right
now, so it survives a scene switch and is cleared by a deliberate act rather
than by putting a different map down. See ADR-018.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config, db, media

log = logging.getLogger("ezvtt.handouts")

SHOWING_KEY = "handout_showing"
MAX_TITLE_LENGTH = 120


def to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "url": f"/media/handouts/{row['filename']}",
        "thumb_url": f"/media/thumbs/handouts/{row['filename']}",
        "width_px": row["width_px"],
        "height_px": row["height_px"],
    }


def add(stored: media.StoredImage, title: str) -> dict[str, Any]:
    conn = db.connect()
    cursor = conn.execute(
        """
        INSERT INTO handouts (title, filename, width_px, height_px)
        VALUES (?, ?, ?, ?)
        """,
        (title.strip()[:MAX_TITLE_LENGTH] or "Handout",
         stored.filename, stored.width, stored.height),
    )
    conn.commit()
    return get(cursor.lastrowid)


def get(handout_id: int) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM handouts WHERE id = ?", (handout_id,)
    ).fetchone()
    return to_dict(row) if row else None


def listing() -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM handouts ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return [to_dict(row) for row in rows]


def rename(handout_id: int, title: str) -> bool:
    title = title.strip()[:MAX_TITLE_LENGTH]
    if not title:
        raise ValueError("A handout needs a title.")

    conn = db.connect()
    cursor = conn.execute(
        "UPDATE handouts SET title = ? WHERE id = ?", (title, handout_id)
    )
    conn.commit()
    return cursor.rowcount > 0


def remove(handout_id: int) -> str | None:
    """Delete a handout. Returns the filename to unlink, or None if unknown."""
    conn = db.connect()
    row = conn.execute(
        "SELECT filename FROM handouts WHERE id = ?", (handout_id,)
    ).fetchone()
    if row is None:
        return None

    conn.execute("DELETE FROM handouts WHERE id = ?", (handout_id,))
    conn.commit()

    # Deleting what is on screen takes it off screen. Leaving the pointer would
    # mean every client asking for an image that is no longer there.
    if showing_id() == handout_id:
        hide()
    return row["filename"]


# --------------------------------------------------------------------------- #
# What the table is looking at
# --------------------------------------------------------------------------- #

def showing_id() -> int | None:
    raw = db.get_setting(SHOWING_KEY, "")
    return int(raw) if raw.isdigit() else None


def showing() -> dict[str, Any] | None:
    """The handout on screen, or None.

    Resolves through ``get`` so a pointer left behind by a deleted row answers
    "nothing is showing" rather than a broken image.
    """
    handout_id = showing_id()
    return get(handout_id) if handout_id is not None else None


def show(handout_id: int) -> dict[str, Any]:
    handout = get(handout_id)
    if handout is None:
        raise ValueError("That handout no longer exists.")

    db.set_setting(SHOWING_KEY, str(handout_id))
    return handout


def hide() -> None:
    db.set_setting(SHOWING_KEY, "")


def file_path(filename: str):
    return config.HANDOUTS_DIR / filename
