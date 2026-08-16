"""The asset library: importing bundled artwork and categorising it.

Bundled art is indexed from ``assets/bundled/`` into the ``assets`` table.
Nothing here ever writes into that directory -- Tom's Open Map License does not
permit editing those files, so footprint is stored as metadata and applied as a
display-time transform over the untouched original. See ADR-005.

Categories are derived from the filename by keyword. They exist so a GM hunting
for a barrel mid-session can narrow 800 tiles to a few dozen; they are a search
aid, not a taxonomy, and a wrong guess costs nothing because search still works.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import config, db
from .grid import display_name, parse_footprint

log = logging.getLogger("ezvtt.assets")

# Ordered: the first rule that matches wins, so specific terms precede general
# ones. "Trap Spike Pit" must land in Traps, not Terrain.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Traps", ("trap", "spike", "pressure plate", "snare")),
    # Before Containers, so "Torturers Cage" is macabre rather than storage.
    ("Macabre", (
        "bone", "skull", "skeleton", "grave", "coffin", "tomb", "cemetery",
        "corpse", "sarcophag", "crypt", "headstone", "mummy", "body bag",
        "blood", "gore", "torturer", "manacle", "iron maiden", "gallows",
        "cage", "barrow", "guillotine", "pyre",
    )),
    ("Magic", (
        "arcane", "portal", "rune", "crystal", "potion", "alchem", "summon",
        "magic", "spell", "orb", "relic", "sigil", "fey", "eldritch", "ritual",
    )),
    ("Lighting", (
        "candle", "lantern", "torch", "brazier", "campfire", "fireplace",
        "chandelier", "lamp", "sconce", "bonfire", "candelabra",
    )),
    ("Books & scrolls", ("book", "scroll", "tome", "quill", "parchment", "ledger")),
    ("Food & drink", (
        "food", "bread", "meat", "ale", "wine", "tankard", "plate", "cup",
        "bottle", "keg", "cheese", "fish", "fruit", "vegetable", "stew",
        "cauldron", "kettle", "teapot", "goblet", "flagon", "mug", "milk",
        "fermenter", "brew",
    )),
    ("Weapons & armour", (
        "sword", "axe", "bow", "shield", "armour", "armor", "weapon", "dagger",
        "spear", "mace", "halberd", "crossbow", "quiver", "helmet", "siege",
        "cannon", "ballista", "catapult", "artillery", "warhorn",
    )),
    # The bundle misspells "Pitchfork" as "Pichfork"; match both.
    ("Farming & animals", (
        "farmer", "hoe", "pitchfork", "pichfork", "rake", "scythe", "sickle",
        "plow", "plough", "crops", "hay ", "straw", "scarecrow", "feeder",
        "bee hive", "beehive", "kennel", "harness", "saddle", "hitching",
        "horse", "trough", "stable", "coop",
    )),
    ("Containers", (
        "barrel", "crate", "chest", "box", "sack", "urn", "pot ", "basket",
        "bucket", "amphora", "jar", "coffer", "trunk", "bin", "backpack",
        "satchel", "jug", "pallet",
    )),
    ("Furniture", (
        "table", "chair", "bed", "couch", "desk", "bookshelf", "shelf",
        "cabinet", "wardrobe", "stool", "bench", "throne", "armchair", "sofa",
        "dresser", "counter", "workbench", "podium", "lectern", "bunk",
        "tavern bar", "sink", "display", "bath",
    )),
    ("Structures", (
        "door", "stair", "wall", "bridge", "pillar", "column", "fence", "gate",
        "arch", "window", "roof", "ladder", "platform", "tower", "hatch",
        "balcony", "railing", "watchtower", "walkway", "well", "stall",
        "teepee", "yurt", "privy", "toilet", "fountain", "grate", "tent",
    )),
    ("Decor", (
        "rug", "carpet", "banner", "painting", "statue", "tapestry", "curtain",
        "mirror", "sign", "vase", "bust", "ornament", "flag", "idol", "canvas",
        "picture frame", "instrument", "harp", "lute", "clock", "orrery",
        "armillary", "globe", "bell", "wing",
    )),
    ("Tools & industry", (
        "anvil", "forge", "tool", "saw", "loom", "wheel", "cart", "wagon",
        "pipe", "gear", "machine", "cog", "valve", "bellows", "grindstone",
        "millstone", "scale", "mortar", "still", "alembic", "crank", "pulley",
        "crane", "capstan", "mangle", "lever", "quarry", "stove", "shovel",
        "broom", "range",
    )),
    ("Nautical", (
        "ship", "boat", "anchor", "sail", "mast", "dock", "oar", "rudder",
        "buoy", "raft", "net ", "harpoon", "barge", "dinghy", "sampan",
        "canoe", "gondola",
    )),
    ("Terrain & water", (
        "water", "lava", "ice", "chasm", "cliff", "pit", "rubble", "sand",
        "snow", "puddle", "pool", "stream", "waterfall", "crack", "hole",
        "mud", "ash", "crater", "travertine", "stalag",
    )),
    ("Nature", (
        "tree", "plant", "bush", "rock", "stone", "log", "vine", "flower",
        "grass", "seaweed", "moss", "mushroom", "root", "branch", "leaf",
        "leaves", "shrub", "hedge", "fern", "coral", "stump", "boulder",
    )),
    ("Rope & cloth", ("rope", "chain", "cloth", "sheet", "tent", "awning", "sack")),
    ("Clutter", (
        "junk", "debris", "broken", "scatter", "pile", "trash", "rubbish",
        "clutter", "scrap", "shard",
    )),
]

FALLBACK_CATEGORY = "Other"


def categorise(name: str) -> str:
    """Best-guess category for an asset name."""
    lowered = f" {name.lower()} "
    for category, keywords in CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return FALLBACK_CATEGORY


def categories() -> list[str]:
    return [category for category, _ in CATEGORY_RULES] + [FALLBACK_CATEGORY]


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def import_bundled(directory: Path | None = None) -> dict[str, int]:
    """Index ``assets/bundled/`` into the assets table.

    Idempotent: rows are keyed by (source, filename), so re-running after the GM
    installs more artwork adds the new files and leaves existing rows -- and any
    footprint the GM has since corrected by hand -- untouched.

    **Reads only.** Nothing is written into the bundle directory (ADR-005).
    """
    directory = directory or config.BUNDLED_ASSETS_DIR
    if not directory.is_dir():
        return {"added": 0, "skipped": 0, "total": 0}

    conn = db.connect()
    existing = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM assets WHERE source = 'bundled'")
    }

    added = 0
    rows: list[tuple[Any, ...]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if path.name in existing:
            continue

        name = display_name(path.name)
        grid_w, grid_h = parse_footprint(path.name)
        rows.append((name, path.name, "bundled", grid_w, grid_h, categorise(name)))
        added += 1

    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO assets
                (name, filename, source, grid_w, grid_h, category)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        log.info("Indexed %d bundled asset(s)", added)

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM assets WHERE source = 'bundled'"
    ).fetchone()["n"]

    return {"added": added, "skipped": len(existing), "total": total}


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #

def asset_to_dict(row) -> dict[str, Any]:
    kind = "bundled" if row["source"] == "bundled" else "uploads"
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "url": f"/media/{kind}/{row['filename']}",
        "thumb_url": f"/media/thumbs/{kind}/{row['filename']}",
        "grid_w": row["grid_w"],
        "grid_h": row["grid_h"],
        "category": row["category"],
    }


def search(
    query: str = "",
    category: str = "",
    limit: int = 120,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the asset library.

    Paged because the bundle alone is 800 entries: rendering every tile at once
    is what makes an asset picker feel slow, and slow is the one thing this
    program cannot be.
    """
    clauses: list[str] = []
    params: list[Any] = []

    query = query.strip()
    if query:
        # LIKE with an escaped pattern rather than string interpolation.
        clauses.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(query)}%")

    if category:
        clauses.append("category = ?")
        params.append(category)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = db.connect()

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM assets {where}",  # noqa: S608 -- clauses are literals
        params,
    ).fetchone()["n"]

    rows = conn.execute(
        f"""
        SELECT * FROM assets {where}
        ORDER BY name COLLATE NOCASE
        LIMIT ? OFFSET ?
        """,  # noqa: S608 -- clauses are literals; values are parameterised
        (*params, max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()

    return {
        "assets": [asset_to_dict(row) for row in rows],
        "total": total,
        "offset": offset,
    }


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search for "50%" is not a match-anything."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_asset(asset_id: int) -> dict[str, Any] | None:
    row = db.connect().execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return asset_to_dict(row) if row else None


def category_counts() -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT category, COUNT(*) AS n FROM assets
        GROUP BY category ORDER BY category
        """
    ).fetchall()
    return [{"category": row["category"] or FALLBACK_CATEGORY, "count": row["n"]} for row in rows]


def rename_asset(asset_id: int, name: str) -> bool:
    name = name.strip()[:120]
    if not name:
        raise ValueError("An asset needs a name.")
    conn = db.connect()
    cursor = conn.execute("UPDATE assets SET name = ? WHERE id = ?", (name, asset_id))
    conn.commit()
    return cursor.rowcount > 0


def resize_asset(asset_id: int, grid_w: float, grid_h: float) -> bool:
    """Change how many grid squares an asset occupies.

    This is metadata only. For bundled Cartos art the image file is never
    touched -- the renderer scales the unmodified original. ADR-005.
    """
    grid_w = max(0.1, min(40.0, float(grid_w)))
    grid_h = max(0.1, min(40.0, float(grid_h)))
    conn = db.connect()
    cursor = conn.execute(
        "UPDATE assets SET grid_w = ?, grid_h = ? WHERE id = ?",
        (grid_w, grid_h, asset_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_asset(asset_id: int) -> str | None:
    """Delete an uploaded asset. Bundled artwork cannot be deleted.

    Removing a bundled row would desynchronise the library from the files on
    disk, and the next import would simply add it back.
    """
    conn = db.connect()
    row = conn.execute(
        "SELECT filename, source FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        return None
    if row["source"] == "bundled":
        raise ValueError(
            "Bundled artwork cannot be deleted. Reinstall or remove the files "
            "with scripts/fetch-assets instead."
        )

    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    conn.commit()
    return row["filename"]
