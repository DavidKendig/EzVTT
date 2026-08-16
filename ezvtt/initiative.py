"""The initiative tracker: turn order, whose turn it is, and what round.

Belongs to a scene, so switching to another encounter and back returns to round
four with the right creature acting rather than to a cleared tracker.

**One ordering function, used everywhere.** ``_ordered`` decides the turn order
and every other operation -- advancing, healing a broken pointer, rendering --
reads it. Two places that sort the same list slightly differently is how a
tracker starts disagreeing with itself about whose turn it is.

An entry may be **concealed** from players, and concealed entries are absent
from their payload rather than flagged in it: a GM rolls the ambush into the
order before the party knows there is one. See ADR-004.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db
from .dice import evaluate

log = logging.getLogger("ezvtt.initiative")

# A tracker longer than this is not a combat, it is a mailing list. The cap
# exists so a stuck client cannot fill the table with rows.
MAX_ENTRIES = 60

MAX_LABEL_LENGTH = 60

# Initiative is a d20 plus a modifier; the bounds are generous enough for any
# houserule and tight enough that nothing overflows a display.
MIN_VALUE, MAX_VALUE = -999.0, 999.0
MIN_MODIFIER, MAX_MODIFIER = -50, 50

ROLL_NOTATION = "1d20"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def entry_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "token_id": row["token_id"],
        "label": row["label"],
        "value": row["value"],
        "modifier": row["modifier"],
        "hidden": bool(row["is_hidden"]),
        "current": bool(row["is_current"]),
    }


def _ordered(scene_id: int) -> list[Any]:
    """Every entry on a scene, in turn order.

    Highest total first; equal totals broken by the higher modifier, which is
    how the rules break them. ``sort_order`` then id keep it deterministic after
    that, so a tie between two identical goblins does not shuffle on each read.
    """
    return db.connect().execute(
        """
        SELECT * FROM initiative
        WHERE scene_id = ?
        ORDER BY value DESC, modifier DESC, sort_order, id
        """,
        (scene_id,),
    ).fetchall()


def round_of(scene_id: int) -> int:
    row = db.connect().execute(
        "SELECT initiative_round FROM scenes WHERE id = ?", (scene_id,)
    ).fetchone()
    return row["initiative_round"] if row else 0


def get(scene_id: int, for_gm: bool = True) -> dict[str, Any]:
    """The tracker as one audience sees it.

    Built per audience rather than filtered on the client, like every other
    payload here. A player is not sent a concealed entry at all, and when the
    creature currently acting is one of them they are sent no ``current_id`` --
    the round still advances on their screen, so they know the fight is moving,
    but not who is moving in it.
    """
    rows = _ordered(scene_id)
    round_number = round_of(scene_id)

    # Repair on read. A token's initiative row cascades away with the token, so
    # killing the creature whose turn it is can leave a running combat with no
    # current entry at all. Rebuilding here means every path that deletes a
    # token -- the hub, a cascade from a scene, a future bulk delete -- is
    # covered without each one having to remember.
    if round_number > 0 and rows and not any(row["is_current"] for row in rows):
        _set_current(rows[0]["id"], scene_id)
        rows = _ordered(scene_id)

    entries = [entry_to_dict(row) for row in rows]
    current = next((e for e in entries if e["current"]), None)

    if for_gm:
        return {
            "round": round_number,
            "entries": entries,
            "current_id": current["id"] if current else None,
        }

    return {
        "round": round_number,
        "entries": [e for e in entries if not e["hidden"]],
        "current_id": current["id"] if current and not current["hidden"] else None,
    }


# --------------------------------------------------------------------------- #
# Building the order
# --------------------------------------------------------------------------- #

def _next_sort_order(scene_id: int) -> int:
    row = db.connect().execute(
        "SELECT COALESCE(MAX(sort_order), 0) AS n FROM initiative WHERE scene_id = ?",
        (scene_id,),
    ).fetchone()
    return row["n"] + 1


def _count(scene_id: int) -> int:
    return db.connect().execute(
        "SELECT COUNT(*) AS n FROM initiative WHERE scene_id = ?", (scene_id,)
    ).fetchone()["n"]


def _unique_label(scene_id: int, label: str) -> str:
    """``Goblin``, then ``Goblin 2``, ``Goblin 3``.

    Four tokens of the same art produce four identical labels, and a tracker
    where three rows read "Goblin" is useless for the one thing it does.
    """
    taken = {
        row["label"] for row in db.connect().execute(
            "SELECT label FROM initiative WHERE scene_id = ?", (scene_id,)
        )
    }
    if label not in taken:
        return label

    for suffix in range(2, MAX_ENTRIES + 2):
        candidate = f"{label} {suffix}"[:MAX_LABEL_LENGTH]
        if candidate not in taken:
            return candidate
    return label


def add(
    scene_id: int,
    label: str,
    value: float = 0.0,
    modifier: int = 0,
    token_id: int | None = None,
    hidden: bool = False,
) -> dict[str, Any]:
    label = str(label).strip()[:MAX_LABEL_LENGTH] or "Someone"

    if _count(scene_id) >= MAX_ENTRIES:
        raise ValueError(f"An initiative order holds at most {MAX_ENTRIES}.")

    conn = db.connect()
    if conn.execute("SELECT 1 FROM scenes WHERE id = ?", (scene_id,)).fetchone() is None:
        raise ValueError("There is no scene to track initiative on.")

    cursor = conn.execute(
        """
        INSERT INTO initiative
            (scene_id, token_id, label, value, modifier, sort_order, is_hidden)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scene_id, token_id, _unique_label(scene_id, label),
            _clamp_value(value), _clamp_modifier(modifier),
            _next_sort_order(scene_id), 1 if hidden else 0,
        ),
    )
    conn.commit()
    return get_entry(cursor.lastrowid)


def add_tokens(scene_id: int, token_ids: list[int]) -> list[dict[str, Any]]:
    """Put tokens into the order, skipping any already in it.

    The fast path: drop four goblins on the map, click once, and the tracker has
    them under their own names. A concealed token joins concealed -- adding the
    ambush to the order must not be what announces it.
    """
    conn = db.connect()
    already = {
        row["token_id"] for row in conn.execute(
            "SELECT token_id FROM initiative WHERE scene_id = ? AND token_id IS NOT NULL",
            (scene_id,),
        )
    }

    added: list[dict[str, Any]] = []
    for token_id in token_ids:
        if token_id in already:
            continue
        row = conn.execute(
            """
            SELECT t.label, t.is_hidden, t.scene_id, a.name AS asset_name
            FROM tokens t LEFT JOIN assets a ON a.id = t.asset_id
            WHERE t.id = ?
            """,
            (token_id,),
        ).fetchone()
        if row is None or row["scene_id"] != scene_id:
            continue

        added.append(add(
            scene_id,
            row["label"] or row["asset_name"] or "Token",
            token_id=token_id,
            hidden=bool(row["is_hidden"]),
        ))
        already.add(token_id)

    return added


def get_entry(entry_id: int) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM initiative WHERE id = ?", (entry_id,)
    ).fetchone()
    return entry_to_dict(row) if row else None


def scene_of(entry_id: int) -> int | None:
    row = db.connect().execute(
        "SELECT scene_id FROM initiative WHERE id = ?", (entry_id,)
    ).fetchone()
    return row["scene_id"] if row else None


def _clamp_value(value: Any) -> float:
    return max(MIN_VALUE, min(MAX_VALUE, float(value)))


def _clamp_modifier(value: Any) -> int:
    return max(MIN_MODIFIER, min(MAX_MODIFIER, int(value)))


def update(entry_id: int, **changes: Any) -> dict[str, Any] | None:
    fields: dict[str, Any] = {}

    if "label" in changes:
        label = str(changes["label"]).strip()[:MAX_LABEL_LENGTH]
        if not label:
            raise ValueError("An entry needs a name.")
        fields["label"] = label

    if "value" in changes:
        fields["value"] = _clamp_value(changes["value"])

    if "modifier" in changes:
        fields["modifier"] = _clamp_modifier(changes["modifier"])

    if "hidden" in changes:
        fields["is_hidden"] = 1 if changes["hidden"] else 0

    if not fields:
        return get_entry(entry_id)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn = db.connect()
    # Column names come from the fixed mapping above, never from client input.
    cursor = conn.execute(
        f"UPDATE initiative SET {assignments} WHERE id = ?",  # noqa: S608
        (*fields.values(), entry_id),
    )
    conn.commit()
    return get_entry(entry_id) if cursor.rowcount else None


def remove(entry_id: int) -> bool:
    """Remove one entry, handing the turn on if it was taking it.

    Worked out *before* the delete, because the successor is defined by an order
    the deleted row is still part of. Killing the creature whose turn it is is
    the single most common reason to remove an entry mid-combat, and the tracker
    has to keep running when it happens.
    """
    scene_id = scene_of(entry_id)
    if scene_id is None:
        return False

    ids = [row["id"] for row in _ordered(scene_id)]
    successor = None
    if get_entry(entry_id)["current"] and len(ids) > 1:
        index = ids.index(entry_id)
        # The next entry in order, or back to the top -- but the round does not
        # tick over. A creature dying is not the table taking a turn.
        successor = ids[index + 1] if index + 1 < len(ids) else ids[0]

    conn = db.connect()
    conn.execute("DELETE FROM initiative WHERE id = ?", (entry_id,))
    conn.commit()

    if successor is not None:
        _set_current(successor, scene_id)
    return True


def sync_token_visibility(token_id: int, hidden: bool) -> int | None:
    """Keep a token's entry as concealed as the token itself.

    Entries inherit concealment when they are added, but a GM reveals the ambush
    by un-hiding the *tokens*. Without this, the creature would appear on the
    map and in the order at different moments -- and worse, hiding a token again
    would leave its name sitting in the players' turn order. Returns the scene
    that changed, or None if the token has no entry.
    """
    conn = db.connect()
    row = conn.execute(
        "SELECT id, scene_id FROM initiative WHERE token_id = ?", (token_id,)
    ).fetchone()
    if row is None:
        return None

    conn.execute(
        "UPDATE initiative SET is_hidden = ? WHERE id = ?",
        (1 if hidden else 0, row["id"]),
    )
    conn.commit()
    return row["scene_id"]


def clear(scene_id: int) -> int:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM initiative WHERE scene_id = ?", (scene_id,))
    conn.execute(
        "UPDATE scenes SET initiative_round = 0 WHERE id = ?", (scene_id,)
    )
    conn.commit()
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Rolling
# --------------------------------------------------------------------------- #

def roll(scene_id: int, entry_id: int | None = None) -> list[dict[str, Any]]:
    """Roll d20 + modifier for one entry, or for the whole order.

    Rolled here, with ``dice.evaluate``, for the same reason chat rolls are:
    there is nowhere in the intent for a client to put a result. See ADR-004.
    """
    rows = _ordered(scene_id)
    if entry_id is not None:
        rows = [row for row in rows if row["id"] == entry_id]
        if not rows:
            raise ValueError("That entry is no longer in the order.")

    conn = db.connect()
    for row in rows:
        total = evaluate(ROLL_NOTATION).total + row["modifier"]
        conn.execute(
            "UPDATE initiative SET value = ? WHERE id = ?",
            (_clamp_value(total), row["id"]),
        )
    conn.commit()

    return [entry_to_dict(row) for row in _ordered(scene_id)]


# --------------------------------------------------------------------------- #
# Running the combat
# --------------------------------------------------------------------------- #

def _set_current(entry_id: int, scene_id: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE initiative SET is_current = 0 WHERE scene_id = ? AND is_current = 1",
            (scene_id,),
        )
        conn.execute("UPDATE initiative SET is_current = 1 WHERE id = ?", (entry_id,))


def _set_round(scene_id: int, round_number: int) -> None:
    conn = db.connect()
    conn.execute(
        "UPDATE scenes SET initiative_round = ? WHERE id = ?",
        (max(0, round_number), scene_id),
    )
    conn.commit()


def start(scene_id: int) -> dict[str, Any]:
    """Begin round one, with whoever is on top acting."""
    rows = _ordered(scene_id)
    if not rows:
        raise ValueError("Add someone to the order first.")

    _set_round(scene_id, 1)
    _set_current(rows[0]["id"], scene_id)
    return get(scene_id)


def stop(scene_id: int) -> dict[str, Any]:
    """End the combat, keeping the order.

    The party is in it, and they are still the party after the fight. Wiping the
    list would mean retyping four names before the next one.
    """
    conn = db.connect()
    conn.execute(
        "UPDATE initiative SET is_current = 0 WHERE scene_id = ?", (scene_id,)
    )
    conn.commit()
    _set_round(scene_id, 0)
    return get(scene_id)


def jump(scene_id: int, entry_id: int) -> dict[str, Any]:
    """Hand the turn straight to one entry.

    "No, we skipped Anya" is the most common correction at a table, and it
    should not require clicking Next four times and rolling the round over on
    the way. The round is untouched; if no combat is running, starting one here
    is what the GM meant by clicking a creature.
    """
    if get_entry(entry_id) is None:
        raise ValueError("That entry is no longer in the order.")

    if round_of(scene_id) == 0:
        _set_round(scene_id, 1)
    _set_current(entry_id, scene_id)
    return get(scene_id)


def advance(scene_id: int, delta: int = 1) -> dict[str, Any]:
    """Hand the turn on, or back.

    Wrapping past the last entry starts the next round; wrapping back past the
    first returns to the previous one. Round one does not go backwards into a
    round zero, because there was no such round to return to -- a misclick at
    the top of a fight should stay where it is rather than quietly end it.
    """
    rows = _ordered(scene_id)
    if not rows:
        raise ValueError("There is nobody in the order.")

    round_number = round_of(scene_id)
    if round_number == 0:
        return start(scene_id)

    ids = [row["id"] for row in rows]
    current = next((row["id"] for row in rows if row["is_current"]), ids[0])
    index = ids.index(current) + (1 if delta >= 0 else -1)

    if index >= len(ids):
        index, round_number = 0, round_number + 1
    elif index < 0:
        if round_number == 1:
            index = 0
        else:
            index, round_number = len(ids) - 1, round_number - 1

    _set_round(scene_id, round_number)
    _set_current(ids[index], scene_id)
    return get(scene_id)
