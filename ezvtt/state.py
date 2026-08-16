"""Reading and mutating the shared table state.

Everything the board shows lives here, between the database and the WebSocket
hub. Route handlers and hub intents both go through these functions so there is
exactly one place that knows how a scene is assembled.

**Player snapshots are built separately from GM snapshots**, not filtered on the
client. A player's snapshot must never contain data they are not allowed to see
-- fog of war in Phase 4 depends on that being true from the start. See ADR-004.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db, media
from .grid import GridSpec, fit_grid

log = logging.getLogger("ezvtt.state")

# Sane bounds for the live grid slider. A grid below a few pixels is invisible
# and pathological to draw; above a few hundred it exceeds any real map's cell.
MIN_GRID_PX = 4.0
MAX_GRID_PX = 1024.0


# --------------------------------------------------------------------------- #
# Maps
# --------------------------------------------------------------------------- #

def map_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": f"/media/maps/{row['filename']}",
        "thumb_url": f"/media/thumbs/maps/{row['filename']}",
        "width_px": row["width_px"],
        "height_px": row["height_px"],
        "grid": {
            "size_px": row["grid_px"],
            "offset_x": row["offset_x"],
            "offset_y": row["offset_y"],
            "color": row["grid_color"],
            "opacity": row["grid_opacity"],
            "visible": bool(row["grid_visible"]),
        },
    }


def list_maps() -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute("SELECT * FROM maps ORDER BY created_at DESC, id DESC").fetchall()
    return [map_to_dict(row) for row in rows]


def get_map(map_id: int) -> dict[str, Any] | None:
    row = db.connect().execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
    return map_to_dict(row) if row else None


def create_map(stored: media.StoredImage, name: str) -> int:
    """Insert a map and guess a starting grid size.

    The guess assumes a 30-square-wide battlemap, which is a common size and is
    close enough that the GM is nudging the slider rather than hunting with it.
    Getting this roughly right is most of "a map on the table in under a minute".
    """
    initial_grid = round(fit_grid(stored.width, stored.height, 30, 30), 2)
    initial_grid = max(MIN_GRID_PX, min(MAX_GRID_PX, initial_grid))

    conn = db.connect()
    cursor = conn.execute(
        """
        INSERT INTO maps (name, filename, width_px, height_px, grid_px)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, stored.filename, stored.width, stored.height, initial_grid),
    )
    conn.commit()
    return cursor.lastrowid


def rename_map(map_id: int, name: str) -> bool:
    name = name.strip()[:120]
    if not name:
        raise ValueError("A map needs a name.")

    conn = db.connect()
    cursor = conn.execute("UPDATE maps SET name = ? WHERE id = ?", (name, map_id))
    conn.commit()
    return cursor.rowcount > 0


def delete_map(map_id: int) -> str | None:
    """Delete a map and its scenes. Returns the filename that should be removed."""
    conn = db.connect()
    row = conn.execute("SELECT filename FROM maps WHERE id = ?", (map_id,)).fetchone()
    if row is None:
        return None

    # Scenes and their tokens cascade from the foreign key.
    conn.execute("DELETE FROM maps WHERE id = ?", (map_id,))
    conn.commit()
    return row["filename"]


def update_grid(map_id: int, **changes: Any) -> dict[str, Any] | None:
    """Apply grid changes to a map, clamping to sane bounds.

    Called on every tick of the GM's slider, so it validates rather than trusts:
    a client sending ``size_px: 0`` would otherwise divide by zero in every
    coordinate conversion downstream.
    """
    fields: dict[str, Any] = {}

    if "size_px" in changes:
        size = float(changes["size_px"])
        fields["grid_px"] = max(MIN_GRID_PX, min(MAX_GRID_PX, size))

    for key, column in (("offset_x", "offset_x"), ("offset_y", "offset_y")):
        if key in changes:
            # Offsets beyond one cell are equivalent to a smaller offset, but
            # clamping generously keeps the UI predictable while dragging.
            fields[column] = max(-MAX_GRID_PX, min(MAX_GRID_PX, float(changes[key])))

    if "opacity" in changes:
        fields["grid_opacity"] = max(0.0, min(1.0, float(changes["opacity"])))

    if "color" in changes:
        color = str(changes["color"]).strip()
        # Only #rgb / #rrggbb reach the database; the value is interpolated into
        # a canvas stroke style on the client.
        if not is_hex_color(color):
            raise ValueError("Grid colour must be a hex value like #000000.")
        fields["grid_color"] = color

    if "visible" in changes:
        fields["grid_visible"] = 1 if changes["visible"] else 0

    if not fields:
        return get_map(map_id)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn = db.connect()
    # Column names come from the fixed mapping above, never from client input.
    cursor = conn.execute(
        f"UPDATE maps SET {assignments} WHERE id = ?",  # noqa: S608
        (*fields.values(), map_id),
    )
    conn.commit()

    return get_map(map_id) if cursor.rowcount else None


def is_hex_color(value: str) -> bool:
    if not value.startswith("#") or len(value) not in (4, 7):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in value[1:])


def grid_spec(map_row: dict[str, Any]) -> GridSpec:
    grid = map_row["grid"]
    return GridSpec(grid["size_px"], grid["offset_x"], grid["offset_y"])


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #

def active_scene() -> dict[str, Any] | None:
    row = db.connect().execute(
        """
        SELECT s.id, s.name, s.map_id
        FROM scenes s
        WHERE s.is_active = 1
        """
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "map_id": row["map_id"]}


def scene_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "map_id": row["map_id"],
        "name": row["name"],
        "active": bool(row["is_active"]),
        "map_name": row["map_name"],
        "thumb_url": f"/media/thumbs/maps/{row['filename']}" if row["filename"] else None,
        "token_count": row["token_count"],
    }


def list_scenes() -> list[dict[str, Any]]:
    """Every prepped scene. GM-only; see ``snapshot``.

    Ordered to match ``list_maps`` so the two sidebar panels agree with each
    other, with a map's own scenes in the order they were created.
    """
    rows = db.connect().execute(
        """
        SELECT s.id, s.map_id, s.name, s.is_active,
               m.name AS map_name, m.filename,
               (SELECT COUNT(*) FROM tokens t WHERE t.scene_id = s.id) AS token_count
        FROM scenes s LEFT JOIN maps m ON m.id = s.map_id
        ORDER BY m.created_at DESC, s.map_id DESC, s.id
        """
    ).fetchall()
    return [scene_to_dict(row) for row in rows]


def get_scene(scene_id: int) -> dict[str, Any] | None:
    row = db.connect().execute(
        """
        SELECT s.id, s.map_id, s.name, s.is_active,
               m.name AS map_name, m.filename,
               (SELECT COUNT(*) FROM tokens t WHERE t.scene_id = s.id) AS token_count
        FROM scenes s LEFT JOIN maps m ON m.id = s.map_id
        WHERE s.id = ?
        """,
        (scene_id,),
    ).fetchone()
    return scene_to_dict(row) if row else None


def create_scene(map_id: int | None, name: str, activate: bool = False) -> int:
    name = name.strip()[:120] or "Scene"

    conn = db.connect()
    if map_id is not None and conn.execute(
        "SELECT 1 FROM maps WHERE id = ?", (map_id,)
    ).fetchone() is None:
        raise ValueError("That map no longer exists.")

    cursor = conn.execute(
        "INSERT INTO scenes (map_id, name, is_active) VALUES (?, ?, 0)",
        (map_id, name),
    )
    scene_id = cursor.lastrowid
    conn.commit()

    if activate:
        activate_scene(scene_id)
    return scene_id


def rename_scene(scene_id: int, name: str) -> bool:
    name = name.strip()[:120]
    if not name:
        raise ValueError("A scene needs a name.")

    conn = db.connect()
    cursor = conn.execute("UPDATE scenes SET name = ? WHERE id = ?", (name, scene_id))
    conn.commit()
    return cursor.rowcount > 0


def duplicate_scene(scene_id: int, name: str | None = None) -> int | None:
    """Copy a scene's layout and fog onto a new scene of the same map.

    "The same room, an hour later" is the common second scene: the furniture is
    already placed and the corridor already revealed, and only the monsters
    differ. Copying the fog matters as much as copying the tokens -- a duplicate
    of a half-explored dungeon that arrives fully concealed is a different scene.

    One transaction, so a copy that fails part-way leaves no scene holding half
    a layout.
    """
    with db.transaction() as conn:
        source = conn.execute(
            "SELECT map_id, name FROM scenes WHERE id = ?", (scene_id,)
        ).fetchone()
        if source is None:
            return None

        copy_name = (name or f"{source['name']} copy").strip()[:120] or "Scene"
        new_id = conn.execute(
            "INSERT INTO scenes (map_id, name, is_active) VALUES (?, ?, 0)",
            (source["map_id"], copy_name),
        ).lastrowid

        conn.execute(
            """
            INSERT INTO tokens (scene_id, asset_id, x, y, grid_w, grid_h, rotation,
                                z, layer, label, owner_user_id, is_hidden, is_locked)
            SELECT ?, asset_id, x, y, grid_w, grid_h, rotation,
                   z, layer, label, owner_user_id, is_hidden, is_locked
            FROM tokens WHERE scene_id = ?
            """,
            (new_id, scene_id),
        )
        conn.execute(
            """
            INSERT INTO fog (scene_id, cols, rows, revealed_rle)
            SELECT ?, cols, rows, revealed_rle FROM fog WHERE scene_id = ?
            """,
            (new_id, scene_id),
        )

    return new_id


def delete_scene(scene_id: int) -> bool:
    """Delete a scene. Its tokens and fog cascade.

    Deleting the scene that is on the table leaves nothing active rather than
    guessing a replacement -- the GM asked to remove what the room is looking
    at, and switching them to some other encounter unasked would be worse than
    an empty board.
    """
    conn = db.connect()
    cursor = conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
    conn.commit()
    return cursor.rowcount > 0


def activate_scene(scene_id: int) -> bool:
    """Make one scene active, deactivating any other.

    Both statements run in a single transaction because a partial unique index
    enforces at most one active scene -- committing the insert before the clear
    would violate it.
    """
    with db.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM scenes WHERE id = ?", (scene_id,)).fetchone()
        if exists is None:
            return False
        conn.execute("UPDATE scenes SET is_active = 0 WHERE is_active = 1")
        # A counter, not a clock: two switches a moment apart record the same
        # timestamp on a coarse platform clock, and the tie then breaks the wrong
        # way when the GM comes back to this map. See 003_scenes.sql.
        latest = conn.execute(
            "SELECT COALESCE(MAX(last_active_seq), 0) AS seq FROM scenes"
        ).fetchone()["seq"]
        conn.execute(
            "UPDATE scenes SET is_active = 1, last_active_seq = ? WHERE id = ?",
            (latest + 1, scene_id),
        )
    return True


def scene_ids_for_map(map_id: int) -> list[int]:
    rows = db.connect().execute(
        "SELECT id FROM scenes WHERE map_id = ?", (map_id,)
    ).fetchall()
    return [row["id"] for row in rows]


def scene_for_map(map_id: int) -> int:
    """The scene to put on the table for a map, created on demand.

    Uploading a map puts it on the table without the GM having to know what a
    scene is, so the first one is made here rather than asked for. With several
    scenes over one map, clicking that map returns to the one last run: picking
    the oldest instead would silently take a GM back to the encounter they
    finished two sessions ago.
    """
    conn = db.connect()
    row = conn.execute(
        """
        SELECT id FROM scenes WHERE map_id = ?
        ORDER BY last_active_seq DESC, id
        LIMIT 1
        """,
        (map_id,),
    ).fetchone()
    if row is not None:
        return row["id"]

    name_row = conn.execute("SELECT name FROM maps WHERE id = ?", (map_id,)).fetchone()
    name = name_row["name"] if name_row else "Scene"
    return create_scene(map_id, name)


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #

LAYERS = ("map", "object", "token")

# Footprint bounds in grid squares. Below a tenth of a square a token is
# invisible and unclickable; above forty it is larger than any sane battlemap.
MIN_FOOTPRINT = 0.1
MAX_FOOTPRINT = 40.0


def scene_bounds(scene_id: int) -> tuple[float, float] | None:
    """The scene's map size in grid squares, or None if it has no map."""
    row = db.connect().execute(
        """
        SELECT m.width_px, m.height_px, m.grid_px, m.offset_x, m.offset_y
        FROM scenes s JOIN maps m ON m.id = s.map_id
        WHERE s.id = ?
        """,
        (scene_id,),
    ).fetchone()
    if row is None or not row["grid_px"]:
        return None

    return (
        (row["width_px"] - row["offset_x"]) / row["grid_px"],
        (row["height_px"] - row["offset_y"]) / row["grid_px"],
    )


def clamp_to_scene(
    scene_id: int, x: float, y: float, grid_w: float, grid_h: float
) -> tuple[float, float]:
    """Keep a token within reach of its map.

    A token may hang over an edge -- half a wagon poking off the road is a
    legitimate thing to want -- but it must not end up thousands of squares away
    where it is invisible and effectively lost. That happens easily: a stale
    zoom or a drop before the board has fitted turns a screen coordinate into a
    wild grid coordinate.
    """
    bounds = scene_bounds(scene_id)
    if bounds is None:
        return x, y

    cols, rows = bounds
    return (
        max(-grid_w, min(cols, x)),
        max(-grid_h, min(rows, y)),
    )


def token_to_dict(
    row, for_gm: bool = True, viewer_id: int | None = None
) -> dict[str, Any]:
    """One token, shaped for the audience receiving it.

    Everything except health is the same for everyone. Health is not: see
    ``status.visible_status`` and ADR-017.
    """
    from . import status

    kind = "bundled" if row["source"] == "bundled" else "uploads"
    return {
        "id": row["id"],
        "scene_id": row["scene_id"],
        "asset_id": row["asset_id"],
        "url": f"/media/{kind}/{row['filename']}" if row["filename"] else None,
        "name": row["asset_name"],
        # Grid units, not pixels: changing the map's grid size must not
        # scatter everything already placed on it.
        "x": row["x"],
        "y": row["y"],
        "grid_w": row["grid_w"],
        "grid_h": row["grid_h"],
        "rotation": row["rotation"],
        "z": row["z"],
        "layer": row["layer"],
        "label": row["label"],
        "owner_user_id": row["owner_user_id"],
        "hidden": bool(row["is_hidden"]),
        "locked": bool(row["is_locked"]),
        **status.visible_status(row, for_gm, viewer_id),
    }


_TOKEN_SELECT = """
    SELECT t.*, a.filename, a.source, a.name AS asset_name
    FROM tokens t
    LEFT JOIN assets a ON a.id = t.asset_id
"""


def list_tokens(
    scene_id: int, for_gm: bool = True, viewer_id: int | None = None
) -> list[dict[str, Any]]:
    """Tokens on a scene, in paint order, shaped for one audience.

    A player's copy omits hidden tokens entirely rather than flagging them -- a
    flag in the JSON is a spoiler for anyone who opens developer tools (ADR-004)
    -- and carries a coarse health bar in place of the numbers, except on the
    token they own (ADR-017).
    """
    clause = "" if for_gm else "AND t.is_hidden = 0"
    rows = db.connect().execute(
        # Layer then z: the CASE keeps map under object under token regardless
        # of how z values happen to collide between layers.
        f"""
        {_TOKEN_SELECT}
        WHERE t.scene_id = ? {clause}
        ORDER BY CASE t.layer WHEN 'map' THEN 0 WHEN 'object' THEN 1 ELSE 2 END,
                 t.z, t.id
        """,  # noqa: S608 -- clause is a literal, not user input
        (scene_id,),
    ).fetchall()
    return [token_to_dict(row, for_gm, viewer_id) for row in rows]


def get_token(
    token_id: int, for_gm: bool = True, viewer_id: int | None = None
) -> dict[str, Any] | None:
    row = db.connect().execute(
        f"{_TOKEN_SELECT} WHERE t.id = ?", (token_id,)  # noqa: S608
    ).fetchone()
    return token_to_dict(row, for_gm, viewer_id) if row else None


def place_token(
    scene_id: int,
    asset_id: int,
    x: float,
    y: float,
    layer: str = "object",
) -> dict[str, Any] | None:
    """Drop an asset onto a scene, inheriting the asset's grid footprint."""
    if layer not in LAYERS:
        raise ValueError(f"Unknown layer: {layer}")

    conn = db.connect()
    asset = conn.execute(
        "SELECT grid_w, grid_h FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if asset is None:
        raise ValueError("That asset no longer exists.")

    if conn.execute("SELECT 1 FROM scenes WHERE id = ?", (scene_id,)).fetchone() is None:
        raise ValueError("There is no scene to place that on.")

    # New tokens land on top of their layer, which is what a GM expects when
    # dropping a rug onto a floor or a barrel next to a wall.
    top = conn.execute(
        "SELECT COALESCE(MAX(z), 0) AS z FROM tokens WHERE scene_id = ? AND layer = ?",
        (scene_id, layer),
    ).fetchone()["z"]

    x, y = clamp_to_scene(scene_id, float(x), float(y),
                          asset["grid_w"], asset["grid_h"])

    cursor = conn.execute(
        """
        INSERT INTO tokens (scene_id, asset_id, x, y, grid_w, grid_h, z, layer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (scene_id, asset_id, x, y, asset["grid_w"], asset["grid_h"], top + 1, layer),
    )
    conn.commit()
    return get_token(cursor.lastrowid)


def update_token(token_id: int, **changes: Any) -> dict[str, Any] | None:
    """Apply changes to a token, validating and clamping each field.

    Called on every frame of a drag, so it validates rather than trusts.
    """
    current = get_token(token_id)
    if current is None:
        return None

    fields: dict[str, Any] = {}

    for key in ("grid_w", "grid_h"):
        if key in changes:
            fields[key] = max(MIN_FOOTPRINT, min(MAX_FOOTPRINT, float(changes[key])))

    if "x" in changes or "y" in changes:
        x, y = clamp_to_scene(
            current["scene_id"],
            float(changes.get("x", current["x"])),
            float(changes.get("y", current["y"])),
            fields.get("grid_w", current["grid_w"]),
            fields.get("grid_h", current["grid_h"]),
        )
        if "x" in changes:
            fields["x"] = x
        if "y" in changes:
            fields["y"] = y

    if "rotation" in changes:
        # Normalised so repeated rotation does not accumulate into huge values.
        fields["rotation"] = float(changes["rotation"]) % 360.0

    if "z" in changes:
        fields["z"] = int(changes["z"])

    if "layer" in changes:
        layer = str(changes["layer"])
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer: {layer}")
        fields["layer"] = layer

    if "label" in changes:
        label = changes["label"]
        fields["label"] = str(label).strip()[:80] if label else None

    if "hidden" in changes:
        fields["is_hidden"] = 1 if changes["hidden"] else 0

    if "locked" in changes:
        fields["is_locked"] = 1 if changes["locked"] else 0

    from . import status

    if "hp" in changes:
        fields["hp"] = status.clean_hp(changes["hp"])

    if "hp_max" in changes:
        fields["hp_max"] = status.clean_hp_max(changes["hp_max"])

    if "hp_public" in changes:
        fields["hp_public"] = 1 if changes["hp_public"] else 0

    if "conditions" in changes:
        # Validated against a fixed vocabulary: these are drawn as badges on a
        # canvas, and "whatever the client sent" is not something to render.
        fields["conditions"] = status.clean_conditions(changes["conditions"])

    if not fields:
        return get_token(token_id)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn = db.connect()
    # Column names come from the fixed mapping above, never from client input.
    cursor = conn.execute(
        f"UPDATE tokens SET {assignments} WHERE id = ?",  # noqa: S608
        (*fields.values(), token_id),
    )
    conn.commit()
    return get_token(token_id) if cursor.rowcount else None


def delete_token(token_id: int) -> bool:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
    conn.commit()
    return cursor.rowcount > 0


def clear_tokens(scene_id: int) -> int:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM tokens WHERE scene_id = ?", (scene_id,))
    conn.commit()
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #

def snapshot(for_gm: bool, viewer_id: int | None = None) -> dict[str, Any]:
    """The full table state a client needs on connect.

    ``for_gm`` decides what is *included*, not what is hidden later on the
    client. A player's snapshot points at a composited map with unrevealed
    cells already painted out, carries no fog mask, and omits tokens standing
    in the dark -- so nothing they are not meant to see reaches their browser
    at all. See ADR-004 and ADR-011.
    """
    from . import fog as fog_module
    from . import handouts as handouts_module
    from . import status as status_module

    scene = active_scene()
    active_map = get_map(scene["map_id"]) if scene and scene["map_id"] else None

    state: dict[str, Any] = {
        # A scene *name* is GM prep. "Ambush at the bridge" in a payload the
        # player's browser can read gives away the evening, so they are told
        # which scene they are on and nothing else about it.
        "scene": scene if for_gm or scene is None
                 else {"id": scene["id"], "map_id": scene["map_id"]},
        "map": active_map,
        "campaign_name": db.get_setting("campaign_name", "A New Campaign"),
        # The condition vocabulary, sent once with the table rather than
        # duplicated in the client. Sixteen entries of no secrecy whatever, and
        # one place for the names to live.
        "conditions": status_module.CONDITIONS,
        # What the table is being shown, if anything. Not a scene property:
        # holding something up outlasts putting a different map down.
        "handout": handouts_module.showing(),
    }

    if scene is None:
        state["tokens"] = []
        state["templates"] = []
        state["initiative"] = {"round": 0, "entries": [], "current_id": None}
        if for_gm:
            state["library"] = list_maps()
            state["scenes"] = list_scenes()
        return state

    from . import aoe as aoe_module
    from . import initiative as initiative_module

    # Before the fail-closed branch below: the turn order is not map data, and a
    # table that cannot be shown the map should still know whose turn it is.
    state["initiative"] = initiative_module.get(scene["id"], for_gm=for_gm)

    fog_state = fog_module.get(scene["id"]) if active_map else None
    tokens = list_tokens(scene["id"], for_gm=for_gm, viewer_id=viewer_id)
    templates = aoe_module.list_for(scene["id"], include_hidden=for_gm)

    if for_gm:
        # The GM gets the real image and the mask, and draws fog as a
        # translucent overlay so they can see what they are concealing.
        state["fog"] = {
            "cols": fog_state["cols"],
            "rows": fog_state["rows"],
            "cells": fog_state["cells"],
            "version": fog_state["version"],
        } if fog_state else None
        state["tokens"] = tokens
        state["templates"] = templates
        # The map library and the scene list are GM tools; players have no use
        # for them and no business seeing what is not on the table.
        state["library"] = list_maps()
        state["scenes"] = list_scenes()
        return state

    if active_map is not None and fog_state is None:
        # Fog could not be computed -- fog.dimensions_for() gives up when a map
        # would need more than MAX_CELLS cells, which a huge map at a tiny grid
        # can reach. Fail CLOSED. Falling through here used to leave the player
        # holding the original map URL and an unfiltered token list, which is
        # exactly the disclosure fog exists to prevent.
        log.warning(
            "No fog state for scene %s; concealing the table from players.",
            scene["id"],
        )
        state["map"] = None
        state["tokens"] = []
        state["templates"] = []
        return state

    if fog_state is not None and active_map is not None:
        # Swap the real image for the composite. The original is never named in
        # a player payload, and /media/maps/ refuses them anyway.
        state["map"] = {
            **active_map,
            "url": f"/media/fog/{scene['id']}?v={fog_state['version']}",
            "thumb_url": None,
        }
        tokens = [
            token for token in tokens
            if fog_module.area_revealed(
                fog_state, token["x"], token["y"], token["grid_w"], token["grid_h"]
            )
        ]
        # A circle drawn over unexplored map is a map of the unexplored part.
        templates = aoe_module.visible_in_fog(templates, fog_state)

    state["tokens"] = tokens
    state["templates"] = templates
    return state
