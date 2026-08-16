"""Grid maths and asset footprint parsing.

The footprint tests run against the real Tom Cartos bundle when it is installed,
because the parser exists to read those exact filenames.
"""

import pytest

from ezvtt import config
from ezvtt.grid import (
    GridSpec,
    display_name,
    distance_feet,
    distance_squares,
    fit_grid,
    parse_footprint,
)

# --------------------------------------------------------------------------- #
# Footprint parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "filename, expected",
    [
        ("TC_Anvil 02_2x1.png", (2, 1)),
        ("TC_Arcane Circle 01_3x3.png", (3, 3)),
        ("TC_Alembic Metal_1x2.png", (1, 2)),
        ("TCM_Academy Globe Silver_1x1.png", (1, 1)),
        ("TC_Wagon_5x13.png", (5, 13)),
        # No suffix: the 304 small props in the bundle default to one square.
        ("TC_Alchemy Cutting Board.png", (1, 1)),
        ("TC_Apothecarys Scales.png", (1, 1)),
        # Pixel dimensions must not be mistaken for a footprint.
        ("Tavern_1920x1080.png", (1, 1)),
        ("map_800x600.jpg", (1, 1)),
        # Degenerate values fall back rather than producing a zero-size token.
        ("thing_0x5.png", (1, 1)),
    ],
)
def test_parse_footprint(filename, expected):
    assert parse_footprint(filename) == expected


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("TC_Apothecary Store Counter_2x1.png", "Apothecary Store Counter"),
        ("TCM_Academy Globe Silver_1x1.png", "Academy Globe Silver"),
        ("TC_Alchemy Cutting Board.png", "Alchemy Cutting Board"),
        # A few bundle files carry a stray leading underscore.
        ("_TC_Bandit Watchtower 01_5x5.png", "Bandit Watchtower 01"),
    ],
)
def test_display_name_strips_prefix_and_suffix(filename, expected):
    assert display_name(filename) == expected


def test_display_name_never_returns_empty():
    """An unnameable asset would render as a blank tile in the library."""
    for odd in ("TC_.png", "_TC_.png", "_1x1.png", ".png", "x.png"):
        assert display_name(odd).strip()


@pytest.mark.skipif(
    not config.BUNDLED_ASSETS_DIR.is_dir()
    or not any(config.BUNDLED_ASSETS_DIR.glob("*.png")),
    reason="Tom Cartos bundle not installed; run scripts/fetch-assets",
)
def test_parser_handles_every_bundled_filename():
    """No real bundle filename may produce an absurd or empty result."""
    for path in config.BUNDLED_ASSETS_DIR.glob("*.png"):
        width, height = parse_footprint(path.name)
        assert 1 <= width <= 40, f"{path.name} -> width {width}"
        assert 1 <= height <= 40, f"{path.name} -> height {height}"
        assert display_name(path.name).strip(), f"{path.name} -> empty name"


# --------------------------------------------------------------------------- #
# Grid geometry
# --------------------------------------------------------------------------- #

def test_cell_lookup_and_origin_round_trip():
    grid = GridSpec(size_px=70)
    assert grid.cell_at(0, 0) == (0, 0)
    assert grid.cell_at(69.9, 69.9) == (0, 0)
    assert grid.cell_at(70, 70) == (1, 1)
    assert grid.cell_origin(2, 3) == (140, 210)


def test_offset_shifts_the_grid():
    grid = GridSpec(size_px=50, offset_x=10, offset_y=20)
    assert grid.cell_at(10, 20) == (0, 0)
    assert grid.cell_at(59, 69) == (0, 0)
    assert grid.cell_at(60, 70) == (1, 1)


def test_points_before_the_origin_land_in_negative_cells():
    """int() would collapse -0.5 onto cell 0 and stack tokens on the edge."""
    grid = GridSpec(size_px=50, offset_x=10, offset_y=10)
    assert grid.cell_at(5, 5) == (-1, -1)


def test_snap_returns_a_cell_corner():
    grid = GridSpec(size_px=64, offset_x=8, offset_y=8)
    assert grid.snap(100, 100) == grid.cell_origin(*grid.cell_at(100, 100))


def test_dimensions_round_up_to_cover_partial_edge_squares():
    """A partial square still holds a token, and fog has to cover it."""
    grid = GridSpec(size_px=100)
    assert grid.dimensions(1000, 500) == (10, 5)
    assert grid.dimensions(1001, 501) == (11, 6)


def test_dimensions_never_returns_zero():
    grid = GridSpec(size_px=100)
    assert grid.dimensions(10, 10) == (1, 1)


def test_zero_or_negative_grid_size_is_rejected():
    # A zero grid size would divide by zero on every coordinate conversion.
    with pytest.raises(ValueError):
        GridSpec(size_px=0)
    with pytest.raises(ValueError):
        GridSpec(size_px=-5)


def test_fit_grid_keeps_squares_square():
    # 1000x500 into 10x10 cannot give square cells; the smaller fit wins.
    assert fit_grid(1000, 500, 10, 10) == 50
    assert fit_grid(1000, 1000, 20, 20) == 50


def test_fit_grid_rejects_nonsense_divisions():
    with pytest.raises(ValueError):
        fit_grid(1000, 1000, 0, 10)


# --------------------------------------------------------------------------- #
# The ruler
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("x1, y1, expected", [
    (3, 0, 3.0),      # straight
    (0, 4, 4.0),
    (3, 3, 3.0),      # a diagonal costs the same as a straight step
    (3, 1, 3.0),      # and a knight's move costs the longer of the two
    (-2, 0, 2.0),     # direction does not matter
])
def test_distance_counts_a_diagonal_as_one_square(x1, y1, expected):
    """Chebyshev, which is the 5e rule and how the table counts movement."""
    assert distance_squares(0, 0, x1, y1) == expected


def test_distance_is_symmetric():
    assert distance_squares(2, 7, 9, 3) == distance_squares(9, 3, 2, 7)


def test_distance_of_a_point_to_itself_is_zero():
    assert distance_squares(4, 4, 4, 4) == 0.0


def test_feet_follow_from_squares():
    assert distance_feet(distance_squares(0, 0, 6, 6)) == 30.0
    assert distance_feet(0.5) == 2.5
