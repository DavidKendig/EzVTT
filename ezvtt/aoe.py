"""Area-of-effect templates: the circle, cone, and line a GM drops on the board.

Called ``aoe`` rather than ``templates`` because ``ezvtt/templates/`` is the
Jinja directory; a ``templates.py`` beside it would be a trap. The wire format
and the interface both say "templates", which is what a GM calls them.

**A template is shared state; a measurement is not.** Dropping a fireball is an
announcement -- the table needs to see whose square it covers -- so it goes
through the server like everything else. The ruler is a question the person
dragging it has, so it never leaves their screen. See ADR-014.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db

log = logging.getLogger("ezvtt.aoe")

KINDS = ("circle", "cone", "line")

# Grid squares. A 60-square radius is larger than any spell and larger than most
# battlemaps; the cap exists so a bad drag cannot produce something that takes a
# second to rasterise on every client.
MIN_SIZE, MAX_SIZE = 0.1, 60.0
MIN_WIDTH, MAX_WIDTH = 0.1, 20.0

# Per scene. Templates are meant to be dropped and cleared, not accumulated.
MAX_TEMPLATES = 40

DEFAULT_COLOR = "#d9a441"
MAX_LABEL_LENGTH = 60


def to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scene_id": row["scene_id"],
        "kind": row["kind"],
        "x": row["x"],
        "y": row["y"],
        "size": row["size"],
        "width": row["width"],
        "angle": row["angle"],
        "color": row["color"],
        "label": row["label"],
        "hidden": bool(row["is_hidden"]),
    }


def list_for(scene_id: int, include_hidden: bool = True) -> list[dict[str, Any]]:
    """Templates on a scene, oldest first.

    ``include_hidden`` is False for players: a concealed template is left out of
    their payload rather than flagged in it, exactly as a hidden token is. A
    trap's blast radius prepped in advance is a spoiler in the shape of a
    circle.
    """
    clause = "" if include_hidden else "AND is_hidden = 0"
    rows = db.connect().execute(
        f"SELECT * FROM aoe_templates WHERE scene_id = ? {clause} ORDER BY id",  # noqa: S608
        (scene_id,),
    ).fetchall()
    return [to_dict(row) for row in rows]


def get(template_id: int) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM aoe_templates WHERE id = ?", (template_id,)
    ).fetchone()
    return to_dict(row) if row else None


def scene_of(template_id: int) -> int | None:
    row = db.connect().execute(
        "SELECT scene_id FROM aoe_templates WHERE id = ?", (template_id,)
    ).fetchone()
    return row["scene_id"] if row else None


def place(
    scene_id: int,
    kind: str,
    x: float,
    y: float,
    size: float,
    angle: float = 0.0,
    width: float = 1.0,
    color: str = DEFAULT_COLOR,
    label: str | None = None,
    hidden: bool = False,
) -> dict[str, Any]:
    """Drop a template on a scene. Every field is validated, not trusted."""
    if kind not in KINDS:
        raise ValueError(f"Unknown template: {kind}")

    conn = db.connect()
    if conn.execute("SELECT 1 FROM scenes WHERE id = ?", (scene_id,)).fetchone() is None:
        raise ValueError("Put a map on the table first.")

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM aoe_templates WHERE scene_id = ?", (scene_id,)
    ).fetchone()["n"]
    if count >= MAX_TEMPLATES:
        raise ValueError(f"That is {MAX_TEMPLATES} templates already. Clear some.")

    cursor = conn.execute(
        """
        INSERT INTO aoe_templates
            (scene_id, kind, x, y, size, width, angle, color, label, is_hidden)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scene_id, kind, float(x), float(y),
            _clamp(size, MIN_SIZE, MAX_SIZE),
            _clamp(width, MIN_WIDTH, MAX_WIDTH),
            float(angle) % 360.0,
            _checked_color(color),
            _clean_label(label),
            1 if hidden else 0,
        ),
    )
    conn.commit()
    return get(cursor.lastrowid)


def update(template_id: int, **changes: Any) -> dict[str, Any] | None:
    fields: dict[str, Any] = {}

    for key in ("x", "y"):
        if key in changes:
            fields[key] = float(changes[key])

    if "size" in changes:
        fields["size"] = _clamp(changes["size"], MIN_SIZE, MAX_SIZE)

    if "width" in changes:
        fields["width"] = _clamp(changes["width"], MIN_WIDTH, MAX_WIDTH)

    if "angle" in changes:
        fields["angle"] = float(changes["angle"]) % 360.0

    if "color" in changes:
        fields["color"] = _checked_color(changes["color"])

    if "label" in changes:
        fields["label"] = _clean_label(changes["label"])

    if "hidden" in changes:
        fields["is_hidden"] = 1 if changes["hidden"] else 0

    if not fields:
        return get(template_id)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn = db.connect()
    # Column names come from the fixed mapping above, never from client input.
    cursor = conn.execute(
        f"UPDATE aoe_templates SET {assignments} WHERE id = ?",  # noqa: S608
        (*fields.values(), template_id),
    )
    conn.commit()
    return get(template_id) if cursor.rowcount else None


def remove(template_id: int) -> bool:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM aoe_templates WHERE id = ?", (template_id,))
    conn.commit()
    return cursor.rowcount > 0


def clear(scene_id: int) -> int:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM aoe_templates WHERE scene_id = ?", (scene_id,))
    conn.commit()
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Fog
# --------------------------------------------------------------------------- #

def reach(template: dict[str, Any]) -> tuple[float, float, float, float]:
    """A box the template cannot extend beyond, in grid units.

    Deliberately generous: a cone or a line reaches at most ``size`` from its
    origin in *some* direction, and squaring that off costs nothing but is
    obviously correct at every angle. Used to decide whether a player standing
    in the dark should be shown it at all.
    """
    span = template["size"] + template["width"]
    return (
        template["x"] - span, template["y"] - span,
        span * 2, span * 2,
    )


def visible_in_fog(templates: list[dict[str, Any]], fog_state: dict | None) -> list[dict]:
    """Drop templates that lie entirely in cells a player has not revealed.

    A circle drawn over unexplored map is a map of the unexplored part. Tokens
    already follow this rule; a template is no different for being a shape
    rather than a picture. See ADR-011.
    """
    if fog_state is None:
        return []

    from . import fog as fog_module

    return [
        template for template in templates
        if fog_module.area_revealed(fog_state, *reach(template))
    ]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _clamp(value: Any, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _clean_label(label: Any) -> str | None:
    if label is None:
        return None
    text = str(label).strip()[:MAX_LABEL_LENGTH]
    return text or None


def _checked_color(color: Any) -> str:
    from .state import is_hex_color

    value = str(color).strip()
    # The value is interpolated into a canvas fill style on every client.
    if not is_hex_color(value):
        raise ValueError("Template colour must be a hex value like #d9a441.")
    return value
