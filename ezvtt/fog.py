"""Fog of war.

Two halves, and the second is the one that makes this real.

**The mask** is a boolean per grid cell, run-length encoded. Compact enough to
rewrite whole on every brush stroke, which keeps the write path trivial.

**The composite** is a copy of the battlemap with unrevealed cells painted out,
generated on the server. Players are served *that*, and never the original.

The second part is the whole point. Drawing black rectangles over a map the
browser has already downloaded is theatre: any player can open the network tab
and look at the untouched image. Fog is an access-control boundary, so the
concealed pixels must not leave the server. See ADR-004 and ADR-011.
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

from PIL import Image

from . import config, db

log = logging.getLogger("ezvtt.fog")

# The composite is what players see. Full resolution is wasted on them -- it is
# a browser canvas, not a print -- and compositing a 12-megapixel battlemap on
# every brush stroke would be far too slow to feel live. 4096px on the long edge
# stays sharp well past any sensible zoom while keeping a rebuild to tens of
# milliseconds.
COMPOSITE_MAX_EDGE = 4096

# Cells beyond this are refused rather than allocated. A pathological grid size
# on a large map could otherwise ask for hundreds of millions of cells.
MAX_CELLS = 4_000_000

# Rebuilding is done off the event loop; one lock per scene so a burst of brush
# strokes collapses into the work that is actually needed.
_rebuild_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(scene_id: int) -> threading.Lock:
    with _locks_guard:
        return _rebuild_locks.setdefault(scene_id, threading.Lock())


# --------------------------------------------------------------------------- #
# Run-length encoding
# --------------------------------------------------------------------------- #

def encode(cells: list[bool]) -> str:
    """Encode a cell list as ``state:count`` runs, e.g. ``0:40,1:8,0:12``.

    Fog is overwhelmingly contiguous -- a revealed room, an unrevealed corridor
    -- so runs compress a 8000-cell mask to a few dozen bytes. Text rather than
    binary so a database dump stays readable when something goes wrong.
    """
    if not cells:
        return ""

    runs: list[str] = []
    current = cells[0]
    length = 0
    for cell in cells:
        if cell == current:
            length += 1
        else:
            runs.append(f"{1 if current else 0}:{length}")
            current = cell
            length = 1
    runs.append(f"{1 if current else 0}:{length}")
    return ",".join(runs)


def decode(encoded: str, expected: int) -> list[bool]:
    """Decode to exactly ``expected`` cells.

    Truncates or pads with False rather than raising: a mask that disagrees with
    the grid means the grid was resized, and losing fog is far better than
    refusing to open the scene at all.
    """
    cells: list[bool] = []
    if encoded:
        for run in encoded.split(","):
            state, _, count = run.partition(":")
            try:
                value = state.strip() == "1"
                repeat = int(count)
            except ValueError:
                continue
            if repeat <= 0:
                continue
            cells.extend([value] * min(repeat, expected - len(cells) + repeat))
            if len(cells) >= expected:
                break

    if len(cells) > expected:
        del cells[expected:]
    elif len(cells) < expected:
        cells.extend([False] * (expected - len(cells)))
    return cells


# --------------------------------------------------------------------------- #
# The mask
# --------------------------------------------------------------------------- #

def dimensions_for(scene_id: int) -> tuple[int, int] | None:
    """Fog grid size for a scene, in cells."""
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

    cols = max(1, math.ceil((row["width_px"] - row["offset_x"]) / row["grid_px"]))
    rows = max(1, math.ceil((row["height_px"] - row["offset_y"]) / row["grid_px"]))

    if cols * rows > MAX_CELLS:
        log.warning("Scene %s would need %d fog cells; refusing", scene_id, cols * rows)
        return None
    return cols, rows


def get(scene_id: int) -> dict | None:
    """The fog state for a scene, created on demand and resized to fit the grid."""
    dims = dimensions_for(scene_id)
    if dims is None:
        return None
    cols, rows = dims

    conn = db.connect()
    row = conn.execute("SELECT * FROM fog WHERE scene_id = ?", (scene_id,)).fetchone()

    if row is None:
        # A new scene starts fully concealed. Revealing is a deliberate act; a
        # map that arrives already visible defeats the point of prepping it.
        conn.execute(
            "INSERT INTO fog (scene_id, cols, rows, revealed_rle) VALUES (?, ?, ?, ?)",
            (scene_id, cols, rows, encode([False] * (cols * rows))),
        )
        conn.commit()
        return {"scene_id": scene_id, "cols": cols, "rows": rows,
                "cells": [False] * (cols * rows), "version": 1}

    cells = decode(row["revealed_rle"], row["cols"] * row["rows"])

    if (row["cols"], row["rows"]) != (cols, rows):
        # The grid changed under an existing mask. Remap by position so a small
        # grid tweak does not scramble an evening's revealing.
        cells = _resize(cells, row["cols"], row["rows"], cols, rows)
        conn.execute(
            """UPDATE fog SET cols = ?, rows = ?, revealed_rle = ?,
                              version = version + 1, updated_at = datetime('now')
               WHERE scene_id = ?""",
            (cols, rows, encode(cells), scene_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM fog WHERE scene_id = ?", (scene_id,)).fetchone()

    return {"scene_id": scene_id, "cols": cols, "rows": rows,
            "cells": cells, "version": row["version"]}


def _resize(cells: list[bool], old_cols: int, old_rows: int,
            cols: int, rows: int) -> list[bool]:
    """Remap a mask onto a new grid, keeping cells that still exist."""
    resized = [False] * (cols * rows)
    for y in range(min(old_rows, rows)):
        for x in range(min(old_cols, cols)):
            resized[y * cols + x] = cells[y * old_cols + x]
    return resized


def _store(scene_id: int, cells: list[bool], cols: int, rows: int) -> int:
    conn = db.connect()
    conn.execute(
        """UPDATE fog SET revealed_rle = ?, cols = ?, rows = ?,
                          version = version + 1, updated_at = datetime('now')
           WHERE scene_id = ?""",
        (encode(cells), cols, rows, scene_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT version FROM fog WHERE scene_id = ?", (scene_id,)
    ).fetchone()
    return row["version"] if row else 1


def paint(scene_id: int, x: float, y: float, radius: float, revealed: bool) -> dict | None:
    """Paint a circular brush of ``radius`` cells centred on a cell coordinate."""
    state = get(scene_id)
    if state is None:
        return None

    cols, rows, cells = state["cols"], state["rows"], state["cells"]
    radius = max(0.5, min(60.0, float(radius)))

    # Square bounds first, then a circular test inside: a round brush is far
    # easier to trace a corridor with than a square one.
    left = max(0, int(math.floor(x - radius)))
    right = min(cols - 1, int(math.ceil(x + radius)))
    top = max(0, int(math.floor(y - radius)))
    bottom = min(rows - 1, int(math.ceil(y + radius)))

    changed = False
    for cy in range(top, bottom + 1):
        for cx in range(left, right + 1):
            # Cell centres, so the brush is symmetric about the cursor.
            if (cx + 0.5 - x) ** 2 + (cy + 0.5 - y) ** 2 <= radius * radius:
                index = cy * cols + cx
                if cells[index] != revealed:
                    cells[index] = revealed
                    changed = True

    if not changed:
        return state
    state["version"] = _store(scene_id, cells, cols, rows)
    return state


def paint_rect(scene_id: int, x0: float, y0: float, x1: float, y1: float,
               revealed: bool) -> dict | None:
    """Reveal or hide a rectangle of cells."""
    state = get(scene_id)
    if state is None:
        return None

    cols, rows, cells = state["cols"], state["rows"], state["cells"]
    left = max(0, int(math.floor(min(x0, x1))))
    right = min(cols - 1, int(math.floor(max(x0, x1))))
    top = max(0, int(math.floor(min(y0, y1))))
    bottom = min(rows - 1, int(math.floor(max(y0, y1))))

    changed = False
    for cy in range(top, bottom + 1):
        for cx in range(left, right + 1):
            index = cy * cols + cx
            if cells[index] != revealed:
                cells[index] = revealed
                changed = True

    if not changed:
        return state
    state["version"] = _store(scene_id, cells, cols, rows)
    return state


def set_all(scene_id: int, revealed: bool) -> dict | None:
    state = get(scene_id)
    if state is None:
        return None
    cells = [revealed] * (state["cols"] * state["rows"])
    if cells == state["cells"]:
        return state
    state["cells"] = cells
    state["version"] = _store(scene_id, cells, state["cols"], state["rows"])
    return state


def is_revealed(state: dict, x: float, y: float) -> bool:
    """Whether the cell containing a grid coordinate has been revealed."""
    cx, cy = int(math.floor(x)), int(math.floor(y))
    if not (0 <= cx < state["cols"] and 0 <= cy < state["rows"]):
        return False
    return state["cells"][cy * state["cols"] + cx]


def area_revealed(state: dict, x: float, y: float, w: float, h: float) -> bool:
    """Whether any cell a token occupies has been revealed.

    Any rather than all: a wagon straddling the edge of a lit room should be
    visible, and a token is not concealed by overhanging into the dark.
    """
    left = max(0, int(math.floor(x)))
    right = min(state["cols"] - 1, int(math.floor(x + max(0.0, w - 0.001))))
    top = max(0, int(math.floor(y)))
    bottom = min(state["rows"] - 1, int(math.floor(y + max(0.0, h - 0.001))))

    for cy in range(top, bottom + 1):
        for cx in range(left, right + 1):
            if state["cells"][cy * state["cols"] + cx]:
                return True
    return False


# --------------------------------------------------------------------------- #
# The composite
# --------------------------------------------------------------------------- #

def composite_path(scene_id: int, version: int) -> Path:
    return config.FOG_DIR / f"scene-{scene_id}-v{version}.webp"


def _map_for_scene(scene_id: int):
    return db.connect().execute(
        """
        SELECT m.filename, m.width_px, m.height_px, m.grid_px, m.offset_x, m.offset_y
        FROM scenes s JOIN maps m ON m.id = s.map_id
        WHERE s.id = ?
        """,
        (scene_id,),
    ).fetchone()


def build_composite(scene_id: int) -> Path | None:
    """Render the map with unrevealed cells painted out, for players.

    Cheap because it runs at composite resolution rather than the source's, and
    because the mask is drawn as one scaled-up image rather than cell by cell.
    """
    state = get(scene_id)
    if state is None:
        return None

    target = composite_path(scene_id, state["version"])
    if target.is_file():
        return target

    row = _map_for_scene(scene_id)
    if row is None:
        return None

    source = config.MAPS_DIR / row["filename"]
    if not source.is_file():
        return None

    lock = _lock_for(scene_id)
    with lock:
        if target.is_file():          # another thread won the race
            return target

        config.FOG_DIR.mkdir(parents=True, exist_ok=True)
        cols, rows = state["cols"], state["rows"]

        try:
            with Image.open(source) as original:
                image = original.convert("RGB")

                scale = min(
                    1.0, COMPOSITE_MAX_EDGE / max(image.width, image.height)
                )
                if scale < 1.0:
                    image = image.resize(
                        (max(1, round(image.width * scale)),
                         max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )

                # One pixel per cell, then scaled to the grid. Far faster than
                # filling thousands of rectangles, and it lands on the same
                # boundaries because the grid is regular.
                mask = Image.new("L", (cols, rows))
                mask.putdata([255 if cell else 0 for cell in state["cells"]])

                cell_px = row["grid_px"] * scale
                mask = mask.resize(
                    (max(1, round(cols * cell_px)), max(1, round(rows * cell_px))),
                    Image.Resampling.NEAREST,
                )

                # The mask starts at the grid offset, so it is pasted onto a
                # black canvas the size of the map rather than used directly.
                full = Image.new("L", image.size, 0)
                full.paste(mask, (round(row["offset_x"] * scale),
                                  round(row["offset_y"] * scale)))

                concealed = Image.new("RGB", image.size, (0, 0, 0))
                concealed.paste(image, mask=full)

                # Lossy on purpose. On a real battlemap this is roughly six
                # times smaller and three times faster than lossless, which
                # matters for an image rebuilt on every brush stroke. The only
                # cost is a unit or two of ringing along the fog boundary --
                # bounded by a test, and carrying no usable information about
                # what is behind it.
                staging = target.with_suffix(".part")
                concealed.save(staging, "WEBP", quality=80, method=2)
                staging.replace(target)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            log.warning("Could not build fog composite for scene %s: %s", scene_id, exc)
            return None

    _prune_old_composites(scene_id, keep=state["version"])
    return target


def _prune_old_composites(scene_id: int, keep: int) -> None:
    """Delete superseded composites.

    A long session brushes fog hundreds of times; without this, every version
    stays on disk. One older file is kept so a request already in flight for the
    previous version is still served rather than 404ing mid-reveal.
    """
    prefix = f"scene-{scene_id}-v"
    try:
        for path in config.FOG_DIR.glob(f"{prefix}*.webp"):
            try:
                version = int(path.stem.rsplit("v", 1)[-1])
            except ValueError:
                continue
            if version < keep - 1:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def clear_composites(scene_id: int) -> None:
    try:
        for path in config.FOG_DIR.glob(f"scene-{scene_id}-v*.webp"):
            path.unlink(missing_ok=True)
    except OSError:
        pass
