"""Reading a grid off the artwork.

Two failures matter, and they are not symmetric. Missing a grid costs the GM
the slider drag they were going to do anyway. **Finding one that is not there
is worse**: the map arrives with squares that do not line up, and nothing says
so -- the GM has to notice before they know to look. So the tests that assert
*silence* on gridless artwork are the important half of this file.

The synthetic maps below are built the way a battlemap is: a cluttered
background with lines drawn over it, sometimes faint, sometimes blurred by the
resizing and re-encoding a map picks up on its way through the internet.
"""

import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from ezvtt import gridfind


def battlemap(
    size: float = 70,
    offset: tuple[int, int] = (13, 27),
    width: int = 2,
    alpha: int = 255,
    clutter: int = 400,
    blur: float = 0.0,
    grid: bool = True,
    seed: int = 3,
    canvas: tuple[int, int] = (1200, 900),
) -> Image.Image:
    """A map with, or without, a grid drawn on it."""
    rnd = random.Random(seed)
    w, h = canvas
    image = Image.new("RGB", (w, h), (120, 104, 84))
    draw = ImageDraw.Draw(image)

    # Furniture, rubble, and floor texture: strong edges that do not repeat.
    for _ in range(clutter):
        x, y = rnd.randrange(w), rnd.randrange(h)
        draw.ellipse(
            [x, y, x + rnd.randrange(4, 40), y + rnd.randrange(4, 40)],
            fill=tuple(rnd.randrange(30, 220) for _ in range(3)),
        )

    if grid:
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        x = offset[0]
        while x < w:
            pen.line([(x, 0), (x, h)], fill=(0, 0, 0, alpha), width=width)
            x += size
        y = offset[1]
        while y < h:
            pen.line([(0, y), (w, y)], fill=(0, 0, 0, alpha), width=width)
            y += size
        image = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")

    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    return image


def tiled(tile: int = 48, canvas: tuple[int, int] = (1200, 900)) -> Image.Image:
    """Artwork with no grid whose *texture* repeats perfectly.

    This is the case that autocorrelation alone gets wrong, and the reason
    `_line_contrast` exists. A real Cartos gravel texture behaves exactly like
    this: a flawless period carrying no lines at all.
    """
    rnd = random.Random(11)
    patch = Image.new("RGB", (tile, tile))
    patch.putdata([
        tuple(rnd.randrange(70, 150) for _ in range(3))
        for _ in range(tile * tile)
    ])
    image = Image.new("RGB", canvas)
    for y in range(0, canvas[1], tile):
        for x in range(0, canvas[0], tile):
            image.paste(patch, (x, y))
    return image


# --------------------------------------------------------------------------- #
# Finding a grid that is there
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size, offset", [
    (70, (13, 27)),
    (50, (0, 0)),
    (100, (45, 90)),
    (128, (7, 3)),
    (40, (21, 11)),
])
def test_a_drawn_grid_is_read_off_the_artwork(size, offset):
    guess = gridfind.detect(battlemap(size=size, offset=offset))

    assert guess is not None
    assert guess.size_px == pytest.approx(size, abs=0.6)
    # Within a couple of pixels: the lines have thickness, and either edge of
    # one is a defensible answer. The GM nudges from here, they do not hunt.
    assert guess.offset_x == pytest.approx(offset[0], abs=2.5)
    assert guess.offset_y == pytest.approx(offset[1], abs=2.5)


def test_the_offset_is_reported_close_to_zero_rather_than_a_whole_square_out():
    """49.5 on a 50px grid draws the same lines as -0.5 and reads as broken."""
    guess = gridfind.detect(battlemap(size=50, offset=(0, 0), width=1))

    assert guess is not None
    assert abs(guess.offset_x) <= 2.5
    assert abs(guess.offset_y) <= 2.5


def test_a_faint_grid_over_heavy_clutter_is_still_found():
    guess = gridfind.detect(battlemap(alpha=30, width=1, clutter=1500))

    assert guess is not None
    assert guess.size_px == pytest.approx(70, abs=0.6)


def test_a_grid_softened_by_resizing_and_re_encoding_is_still_found():
    guess = gridfind.detect(battlemap(alpha=60, width=1, clutter=1500, blur=1.2))

    assert guess is not None
    assert guess.size_px == pytest.approx(70, abs=1.0)


def test_a_grid_survives_the_downscale_to_working_resolution():
    """A 12-megapixel battlemap holds no more grid than a small one."""
    guess = gridfind.detect(battlemap(size=210, offset=(30, 45), width=6,
                                      canvas=(3600, 2700)))

    assert guess is not None
    assert guess.size_px == pytest.approx(210, abs=3.0)


def test_the_guess_is_in_source_pixels_not_working_pixels():
    big = gridfind.detect(battlemap(size=140, offset=(20, 20), width=4,
                                    canvas=(2400, 1800)))
    small = gridfind.detect(battlemap(size=70, offset=(10, 10), width=2,
                                      canvas=(1200, 900)))

    assert big is not None and small is not None
    assert big.size_px == pytest.approx(small.size_px * 2, rel=0.05)


# --------------------------------------------------------------------------- #
# Staying quiet about a grid that is not
# --------------------------------------------------------------------------- #

def test_a_map_with_no_grid_is_left_alone():
    assert gridfind.detect(battlemap(grid=False, clutter=1500)) is None


def test_flat_artwork_is_left_alone():
    assert gridfind.detect(battlemap(grid=False, clutter=0)) is None


def test_a_repeating_texture_is_not_mistaken_for_a_grid():
    """The failure this feature is most likely to make, and the worst one."""
    assert gridfind.detect(tiled()) is None


def test_lines_on_one_axis_only_are_not_a_grid():
    """Floorboards, panelling, a colonnade. Squares are square."""
    image = battlemap(grid=False, clutter=600)
    draw = ImageDraw.Draw(image)
    for x in range(11, image.width, 64):
        draw.line([(x, 0), (x, image.height)], fill=(30, 24, 18), width=2)

    assert gridfind.detect(image) is None


def test_a_thumbnail_sized_image_is_left_alone():
    assert gridfind.detect(battlemap(canvas=(120, 90))) is None


def test_unreadable_input_is_an_answer_of_none_not_an_exception(tmp_path):
    broken = tmp_path / "not-an-image.png"
    broken.write_bytes(b"this is not a PNG")

    assert gridfind.detect(broken) is None


# --------------------------------------------------------------------------- #
# The pieces
# --------------------------------------------------------------------------- #

def test_contrast_separates_a_drawn_line_from_a_texture():
    # Flat profile: every position is as good as every other -- a texture.
    flat = [10.0] * 600
    assert gridfind._line_contrast(flat, 50, 0) == pytest.approx(1.0)

    # Spikes every 50: a drawn grid.
    spiked = [100.0 if i % 50 == 0 else 5.0 for i in range(600)]
    assert gridfind._line_contrast(spiked, 50, 0) > 10


def test_a_harmonic_is_folded_down_to_the_real_spacing():
    """A grid at 70 also correlates at 140, and a grid twice too large looks
    plausible while being silently wrong."""
    scores = [0.0] * 200
    scores[70] = 0.9
    scores[140] = 0.95

    assert gridfind._fundamental(scores, 140) == 70


def test_a_harmonic_is_not_folded_when_the_shorter_spacing_is_weak():
    scores = [0.0] * 200
    scores[70] = 0.2
    scores[140] = 0.95

    assert gridfind._fundamental(scores, 140) == 140


def test_axes_that_disagree_are_not_trusted():
    """One convincing axis is half an answer, and half is below the threshold."""
    _, agreeing = gridfind._combine(70.0, 0.9, 70.5, 0.9)
    _, disagreeing = gridfind._combine(70.0, 0.9, 31.0, 0.9)

    assert agreeing > disagreeing * 1.5
    assert disagreeing <= 0.5


def test_confidence_is_reported_and_bounded():
    guess = gridfind.detect(battlemap())

    assert guess is not None
    assert gridfind.MIN_CONFIDENCE <= guess.confidence <= 1.0


def test_as_changes_matches_what_update_grid_takes():
    guess = gridfind.GridGuess(size_px=70.0, offset_x=1.0, offset_y=2.0, confidence=0.9)
    assert guess.as_changes() == {"size_px": 70.0, "offset_x": 1.0, "offset_y": 2.0}
