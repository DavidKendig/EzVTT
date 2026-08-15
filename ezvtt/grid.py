"""Grid maths and asset footprint parsing.

Pure functions, no I/O -- everything here is directly testable, which is why the
fiddly coordinate conversions live here rather than inline in route handlers.

Positions are stored in *grid units*, not pixels, so that changing a map's
grid size does not scatter everything already placed on it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Trailing "_2x1" on an asset filename gives its footprint in grid squares.
# The Tom Cartos bundle uses this consistently: of 800 files, 496 carry a
# suffix and 304 do not (those are small props that default to 1x1).
_FOOTPRINT_RE = re.compile(r"_(\d{1,3})x(\d{1,3})$")

# Guard against a filename like "Tavern_1920x1080" being read as a 1920-square
# footprint. Nothing on a battlemap is 40 squares across; those are pixel
# dimensions that happen to match the pattern.
MAX_FOOTPRINT = 40


def parse_footprint(filename: str) -> tuple[int, int]:
    """Grid footprint encoded in an asset filename, defaulting to 1x1.

    >>> parse_footprint("TC_Anvil 02_2x1.png")
    (2, 1)
    >>> parse_footprint("TC_Alchemy Cutting Board.png")
    (1, 1)
    >>> parse_footprint("Map_1920x1080.png")   # pixel dimensions, not squares
    (1, 1)
    """
    stem = filename.rsplit(".", 1)[0]
    match = _FOOTPRINT_RE.search(stem)
    if not match:
        return (1, 1)

    width, height = int(match.group(1)), int(match.group(2))
    if not (1 <= width <= MAX_FOOTPRINT and 1 <= height <= MAX_FOOTPRINT):
        return (1, 1)
    return (width, height)


def display_name(filename: str) -> str:
    """Human-readable asset name from a bundle filename.

    Strips the vendor prefix and the footprint suffix, both of which are noise
    in a library the GM is searching under time pressure mid-session.

    >>> display_name("TC_Apothecary Store Counter_2x1.png")
    'Apothecary Store Counter'
    >>> display_name("TCM_Academy Globe Silver_1x1.png")
    'Academy Globe Silver'
    """
    stem = filename.rsplit(".", 1)[0]
    stem = _FOOTPRINT_RE.sub("", stem)
    # Vendor prefixes: TC_ (Tom Cartos) and TCM_ as used in the bundle. A few
    # files carry a stray leading underscore ("_TC_Bandit Watchtower 01_5x5"),
    # so allow one before the prefix and strip any that survive.
    stem = re.sub(r"^_?TCM?_", "", stem)
    return stem.strip(" _") or filename


@dataclass(frozen=True)
class GridSpec:
    """A map's grid geometry, in source-image pixels."""

    size_px: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def __post_init__(self) -> None:
        if self.size_px <= 0:
            raise ValueError(f"Grid size must be positive, got {self.size_px}")

    def cell_at(self, px: float, py: float) -> tuple[int, int]:
        """Grid cell containing an image-pixel point.

        Uses floor rather than int() so that points left of or above the grid
        origin land in negative cells instead of collapsing onto zero -- offsets
        routinely put the first row of squares at a negative coordinate.
        """
        return (
            math.floor((px - self.offset_x) / self.size_px),
            math.floor((py - self.offset_y) / self.size_px),
        )

    def cell_origin(self, col: int, row: int) -> tuple[float, float]:
        """Top-left image-pixel corner of a grid cell."""
        return (
            self.offset_x + col * self.size_px,
            self.offset_y + row * self.size_px,
        )

    def snap(self, px: float, py: float) -> tuple[float, float]:
        """Snap an image-pixel point to the nearest cell corner."""
        col, row = self.cell_at(px, py)
        return self.cell_origin(col, row)

    def dimensions(self, width_px: float, height_px: float) -> tuple[int, int]:
        """Columns and rows needed to cover an image of this size.

        Rounded up: a partial square at the right or bottom edge is still a
        square a token can stand on, and fog must cover it.
        """
        return (
            max(1, math.ceil((width_px - self.offset_x) / self.size_px)),
            max(1, math.ceil((height_px - self.offset_y) / self.size_px)),
        )


# How far one square is, for the ruler's second figure. Five feet is the
# assumption of every edition this program is likely to sit in front of; a map
# drawn to some other scale still measures correctly in squares, which is the
# figure shown first.
FEET_PER_SQUARE = 5.0


def distance_squares(x0: float, y0: float, x1: float, y1: float) -> float:
    """Distance in grid squares, counting a diagonal as one square.

    Chebyshev, which is the 5e rule and the one a table will be using unless
    they have deliberately chosen otherwise. Euclidean would read as more
    precise while disagreeing with how the group actually counts movement, and
    the alternating 5-10-5 of older editions is a house rule this does not try
    to guess at.

    >>> distance_squares(0, 0, 3, 0)
    3.0
    >>> distance_squares(0, 0, 3, 3)   # a diagonal costs the same
    3.0
    """
    return float(max(abs(x1 - x0), abs(y1 - y0)))


def distance_feet(squares: float) -> float:
    return squares * FEET_PER_SQUARE


def fit_grid(width_px: float, height_px: float, cols: int, rows: int) -> float:
    """Grid size that divides an image into roughly cols x rows squares.

    Squares must stay square, so the smaller of the two axis fits wins --
    otherwise a map whose aspect ratio does not match the requested division
    would produce rectangles.
    """
    if cols <= 0 or rows <= 0:
        raise ValueError("Column and row counts must be positive")
    return min(width_px / cols, height_px / rows)
