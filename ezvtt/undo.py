"""Undo and redo for the GM's board edits.

**A checkpoint is a snapshot of the scene, not a description of the change.**
Tokens, templates, and the fog mask, captured before an edit and restored
wholesale if the GM takes it back. Inverse operations would be smaller, but
every one of them is a separate chance to be subtly wrong -- "undo a delete" has
to put the row back with its id, its z, its owner and its conditions, and an
inverse that forgets one field fails silently, weeks later, in front of a table.
A snapshot cannot half-apply. See ADR-020.

A scene's worth of tokens is a few tens of kilobytes, so a bounded stack of
them costs less than the fog composite that is already on disk.

**In memory, and per scene.** Undo history is a property of the session, not of
the campaign: a GM who closes EzVTT and reopens it expects the table restored,
not the last hour of edits still waiting to be taken back.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from . import db

log = logging.getLogger("ezvtt.undo")

# Deep enough to cover a mistake noticed a few actions later, shallow enough
# that the memory is unremarkable.
MAX_DEPTH = 40

# Edits that arrive in a stream -- dragging a token, sweeping the fog brush --
# would otherwise leave one checkpoint per animation frame, and undo would move
# the token one pixel. Repeats of the same label inside this window collapse
# into the first, which is the state before the drag began.
COALESCE_SECONDS = 1.2


@dataclass
class Checkpoint:
    label: str
    at: float
    tokens: list[tuple]
    templates: list[tuple]
    initiative: list[tuple]
    fog: tuple[int, int, str] | None


@dataclass
class History:
    done: list[Checkpoint] = field(default_factory=list)
    undone: list[Checkpoint] = field(default_factory=list)
    last_label: str = ""
    last_at: float = 0.0


_histories: dict[int, History] = {}


def _history(scene_id: int) -> History:
    return _histories.setdefault(scene_id, History())


def forget(scene_id: int | None = None) -> None:
    """Drop history -- for one scene, or all of it."""
    if scene_id is None:
        _histories.clear()
    else:
        _histories.pop(scene_id, None)


# --------------------------------------------------------------------------- #
# Capturing and restoring
# --------------------------------------------------------------------------- #

_TOKEN_COLUMNS = (
    "id", "scene_id", "asset_id", "x", "y", "grid_w", "grid_h", "rotation", "z",
    "layer", "label", "owner_user_id", "is_hidden", "is_locked",
    "hp", "hp_max", "hp_public", "conditions",
)

_TEMPLATE_COLUMNS = (
    "id", "scene_id", "kind", "x", "y", "size", "width", "angle", "color",
    "label", "is_hidden",
)

# The turn order belongs to a scene as much as its tokens do. Without it here,
# undoing the deletion of the creature whose turn it was brought the token back
# and left the tracker one entry short: the initiative row had cascaded away
# with the token, and nothing put it back.
_INITIATIVE_COLUMNS = (
    "id", "scene_id", "token_id", "label", "value", "sort_order", "is_current",
    "modifier", "is_hidden",
)


def capture(scene_id: int, label: str) -> Checkpoint:
    conn = db.connect()
    tokens = [
        tuple(row) for row in conn.execute(
            f"SELECT {', '.join(_TOKEN_COLUMNS)} FROM tokens WHERE scene_id = ?",  # noqa: S608
            (scene_id,),
        )
    ]
    templates = [
        tuple(row) for row in conn.execute(
            f"SELECT {', '.join(_TEMPLATE_COLUMNS)} FROM aoe_templates WHERE scene_id = ?",  # noqa: S608
            (scene_id,),
        )
    ]
    order = [
        tuple(row) for row in conn.execute(
            f"SELECT {', '.join(_INITIATIVE_COLUMNS)} FROM initiative WHERE scene_id = ?",  # noqa: S608
            (scene_id,),
        )
    ]
    fog_row = conn.execute(
        "SELECT cols, rows, revealed_rle FROM fog WHERE scene_id = ?", (scene_id,)
    ).fetchone()

    return Checkpoint(
        label=label,
        at=time.monotonic(),
        tokens=tokens,
        templates=templates,
        initiative=order,
        fog=tuple(fog_row) if fog_row else None,
    )


def _without_missing(rows: list[tuple], column: int, known: set[int]) -> list[tuple]:
    """Blank out references to rows that no longer exist.

    A snapshot remembers which asset a token was made from, and which token an
    initiative entry belongs to. Either can be deleted between the checkpoint
    and the undo -- removing artwork from the library is an ordinary thing to
    do -- and re-inserting a row pointing at it fails the foreign key and aborts
    the whole restore. The live schema answers this with ON DELETE SET NULL;
    this is the same answer, applied on the way back in.
    """
    cleaned = []
    for row in rows:
        if row[column] is None or row[column] in known:
            cleaned.append(row)
            continue
        patched = list(row)
        patched[column] = None
        cleaned.append(tuple(patched))
    return cleaned


def _restore(scene_id: int, snapshot: Checkpoint) -> None:
    """Put a scene back exactly as it was.

    One transaction: a board half restored is worse than one not restored at
    all, and this runs while five other people are looking at it.
    """
    try:
        with db.transaction() as conn:
            assets = {row["id"] for row in conn.execute("SELECT id FROM assets")}

            conn.execute("DELETE FROM tokens WHERE scene_id = ?", (scene_id,))
            conn.executemany(
                f"INSERT INTO tokens ({', '.join(_TOKEN_COLUMNS)}) "  # noqa: S608
                f"VALUES ({', '.join('?' * len(_TOKEN_COLUMNS))})",
                _without_missing(
                    snapshot.tokens, _TOKEN_COLUMNS.index("asset_id"), assets
                ),
            )

            conn.execute("DELETE FROM aoe_templates WHERE scene_id = ?", (scene_id,))
            conn.executemany(
                f"INSERT INTO aoe_templates ({', '.join(_TEMPLATE_COLUMNS)}) "  # noqa: S608
                f"VALUES ({', '.join('?' * len(_TEMPLATE_COLUMNS))})",
                snapshot.templates,
            )

            # After the tokens, and checked against the ones that actually came
            # back: an entry is tied to its token by foreign key.
            restored = {row[0] for row in snapshot.tokens}
            conn.execute("DELETE FROM initiative WHERE scene_id = ?", (scene_id,))
            conn.executemany(
                f"INSERT INTO initiative ({', '.join(_INITIATIVE_COLUMNS)}) "  # noqa: S608
                f"VALUES ({', '.join('?' * len(_INITIATIVE_COLUMNS))})",
                _without_missing(
                    snapshot.initiative,
                    _INITIATIVE_COLUMNS.index("token_id"),
                    restored,
                ),
            )

            if snapshot.fog is not None:
                cols, rows, rle = snapshot.fog
                # The version keeps climbing even as the mask goes backwards: it
                # is a cache key for the composited image, and reusing a number
                # would serve every player the fog they had a moment ago.
                conn.execute(
                    """UPDATE fog SET cols = ?, rows = ?, revealed_rle = ?,
                                      version = version + 1, updated_at = datetime('now')
                       WHERE scene_id = ?""",
                    (cols, rows, rle, scene_id),
                )
            else:
                # There was no mask when this checkpoint was taken, which is how
                # a scene nobody has brushed yet looks. Removing the row puts
                # that back -- fog.get rebuilds it fully concealed, which is
                # exactly the state before the stroke being undone. Leaving it
                # alone, as this did, meant the *first* brush stroke on a scene
                # could never be taken back.
                conn.execute("DELETE FROM fog WHERE scene_id = ?", (scene_id,))
    except sqlite3.IntegrityError as exc:
        # The scene itself has gone, most likely. Answer the GM rather than
        # letting a database error travel up and close their socket.
        log.warning("Could not restore scene %s: %s", scene_id, exc)
        raise ValueError("That edit cannot be undone any more.") from exc


# --------------------------------------------------------------------------- #
# The stack
# --------------------------------------------------------------------------- #

def checkpoint(scene_id: int, label: str) -> None:
    """Remember how the scene looks *before* an edit.

    Called by the hub ahead of each undoable intent. Repeats of the same label
    inside the coalescing window are dropped, so a drag is one checkpoint
    rather than sixty.
    """
    history = _history(scene_id)
    now = time.monotonic()

    if label == history.last_label and now - history.last_at < COALESCE_SECONDS:
        history.last_at = now
        return

    history.done.append(capture(scene_id, label))
    del history.done[:-MAX_DEPTH]
    # A new edit is a new branch: what was undone cannot be redone onto it.
    history.undone.clear()
    history.last_label = label
    history.last_at = now


def undo(scene_id: int) -> str | None:
    """Take back the last edit. Returns its label, or None if there is nothing."""
    history = _history(scene_id)
    if not history.done:
        return None

    snapshot = history.done.pop()
    # Where we are now becomes the thing redo puts back.
    history.undone.append(capture(scene_id, snapshot.label))
    _restore(scene_id, snapshot)
    history.last_label = ""
    log.info("Undid %s on scene %s", snapshot.label, scene_id)
    return snapshot.label


def redo(scene_id: int) -> str | None:
    history = _history(scene_id)
    if not history.undone:
        return None

    snapshot = history.undone.pop()
    history.done.append(capture(scene_id, snapshot.label))
    _restore(scene_id, snapshot)
    history.last_label = ""
    log.info("Redid %s on scene %s", snapshot.label, scene_id)
    return snapshot.label


def depth(scene_id: int) -> dict[str, Any]:
    """What the GM's buttons should look like."""
    history = _history(scene_id)
    return {
        "undo": len(history.done),
        "redo": len(history.undone),
        "next_undo": history.done[-1].label if history.done else None,
        "next_redo": history.undone[-1].label if history.undone else None,
    }
