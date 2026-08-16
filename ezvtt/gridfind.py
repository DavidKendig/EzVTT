"""Reading a battlemap's grid off the artwork.

The single biggest win for "a map on the table in under a minute": the GM drops
an image and the squares already line up, instead of hunting with a slider.

**How it works.** A drawn grid is the only strongly periodic thing on a
battlemap. Sum the vertical edge strength down each column and you get a signal
that spikes every time a vertical grid line passes -- furniture, walls, and
texture contribute noise that does not repeat. Autocorrelating that signal finds
the spacing; a phase sweep at that spacing finds where the first line sits. The
same again across rows, and the two answers must agree, because grid squares are
square.

**No numpy.** Pillow's BOX resize collapses an image to one row or one column in
C, which is the only expensive part; the rest is a few hundred thousand
multiply-adds over a list, which is fast enough to run on upload. Adding a
numeric stack for this would be a packaging risk and an acknowledgement entry
for no user-visible gain. See ADR-015.

**It says when it does not know.** A featureless or gridless map returns None
rather than a number, and the caller keeps the existing guess. A confidently
wrong grid is worse than no grid: the GM would have to notice it is wrong before
knowing to fix it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

log = logging.getLogger("ezvtt.gridfind")

# Detection runs at this resolution at most. A 12-megapixel battlemap holds no
# more grid information than a 1400px one, and the profiles get 8x cheaper.
MAX_EDGE = 1400

# Spacings considered, in working pixels. Below 12 there would have to be more
# than a hundred squares across the map, which is not a battlemap -- but gravel
# and floorboards do repeat at that scale, and that is what gets found instead.
# Above 300 there are too few repeats on screen to call it periodic at all.
MIN_PERIOD = 12.0
MAX_PERIOD = 300.0

# A map must show at least this many squares along an axis for a spacing to be
# believable. Three repeats of anything is a coincidence.
MIN_REPEATS = 5

# Below this, say nothing. Calibrated against drawn grids (which score well
# above it) and against artwork with no grid at all (which does not).
MIN_CONFIDENCE = 0.30

# How much stronger the edge signal must be *on* the detected lines than across
# the map as a whole. This is the test that separates a drawn grid from a
# repeating texture, and it is the one that matters: a tiling dirt texture
# autocorrelates beautifully at its tile size while carrying no lines at all.
# Measured: drawn grids score 3 to 39, a Cartos gravel texture scores 1.01.
CONTRAST_FLOOR = 1.6
CONTRAST_FULL = 4.0

# Two axes are the same spacing if they are within this of each other. A hand
# drawn grid is not exact, and the source image may have been resized.
AXIS_TOLERANCE = 0.06


@dataclass(frozen=True)
class GridGuess:
    """A detected grid, in *source image* pixels."""

    size_px: float
    offset_x: float
    offset_y: float
    confidence: float

    def as_changes(self) -> dict[str, float]:
        return {
            "size_px": self.size_px,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
        }


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #

def _edge_profiles(image: Image.Image) -> tuple[list[float], list[float]]:
    """Mean vertical-edge strength per column, and horizontal per row.

    The difference between the image and itself shifted one pixel is the
    gradient along that axis; a BOX resize down to a single row (or column)
    averages it, in C, in one pass.
    """
    gray = image.convert("L")
    width, height = gray.size

    vertical = ImageChops.difference(
        gray.crop((1, 0, width, height)), gray.crop((0, 0, width - 1, height))
    )
    horizontal = ImageChops.difference(
        gray.crop((0, 1, width, height)), gray.crop((0, 0, width, height - 1))
    )

    # tobytes() rather than getdata(): one byte per pixel in mode "L", stable
    # across every Pillow this project supports, and getdata is deprecated for
    # removal in Pillow 14.
    columns = vertical.resize((width - 1, 1), Image.Resampling.BOX).tobytes()
    rows = horizontal.resize((1, height - 1), Image.Resampling.BOX).tobytes()
    return [float(v) for v in columns], [float(v) for v in rows]


def _centre(profile: list[float]) -> list[float]:
    """Remove the mean, so autocorrelation measures repetition, not brightness."""
    if not profile:
        return []
    mean = sum(profile) / len(profile)
    return [value - mean for value in profile]


# --------------------------------------------------------------------------- #
# Spacing
# --------------------------------------------------------------------------- #

def _autocorrelation(profile: list[float], max_lag: int) -> list[float]:
    """Normalised autocorrelation for every lag up to ``max_lag``.

    Normalised per lag rather than globally: without it, short lags win simply
    by overlapping more of the signal, and every grid is detected as texture.
    """
    n = len(profile)
    squares = [0.0]
    for value in profile:
        squares.append(squares[-1] + value * value)

    scores = [0.0] * (max_lag + 1)
    for lag in range(1, max_lag + 1):
        overlap = n - lag
        if overlap < lag * MIN_REPEATS:
            break
        total = 0.0
        for i in range(overlap):
            total += profile[i] * profile[i + lag]
        left = squares[overlap] - squares[0]
        right = squares[n] - squares[lag]
        norm = math.sqrt(left * right)
        scores[lag] = total / norm if norm > 0 else 0.0
    return scores


def _fundamental(scores: list[float], best: int) -> int:
    """Fold a peak down to the shortest spacing that explains it.

    A grid at 70px also correlates at 140 and 210, and on a map with heavy
    texture the double can win outright. Every sub-multiple that still scores
    respectably is preferred, because a grid twice too large looks plausible
    and is silently wrong.
    """
    candidate = best
    for divisor in (2, 3, 4, 5):
        folded = round(best / divisor)
        if folded < MIN_PERIOD or folded >= len(scores):
            continue
        if scores[folded] >= scores[best] * 0.7:
            candidate = min(candidate, folded)
    return candidate


def _refine(scores: list[float], peak: int) -> float:
    """Sub-pixel spacing by fitting a parabola through the peak.

    Worth doing: half a pixel of error per square compounds to half a square
    over a thirty-square map, which is exactly the drift a GM notices at the far
    edge and cannot fix with the offset.
    """
    if peak <= 1 or peak + 1 >= len(scores):
        return float(peak)
    left, centre, right = scores[peak - 1], scores[peak], scores[peak + 1]
    denominator = left - 2 * centre + right
    if denominator == 0:
        return float(peak)
    shift = 0.5 * (left - right) / denominator
    return float(peak) + max(-0.5, min(0.5, shift))


def _spacing(profile: list[float]) -> tuple[float, float]:
    """Best spacing in this profile, and how strongly it repeats."""
    centred = _centre(profile)
    max_lag = int(min(MAX_PERIOD, len(centred) / MIN_REPEATS))
    if max_lag < MIN_PERIOD:
        return 0.0, 0.0

    scores = _autocorrelation(centred, max_lag)
    window = scores[int(MIN_PERIOD):]
    if not window:
        return 0.0, 0.0

    best = int(MIN_PERIOD) + window.index(max(window))
    if scores[best] <= 0:
        return 0.0, 0.0

    best = _fundamental(scores, best)
    return _refine(scores, best), scores[best]


# --------------------------------------------------------------------------- #
# Phase
# --------------------------------------------------------------------------- #

def _phase(profile: list[float], period: float) -> float:
    """Where the first grid line sits, in working pixels within one period.

    Swept at quarter-pixel steps rather than solved: the profile is a list of
    integers and its peaks are a pixel or two wide, so there is nothing finer to
    resolve and a sweep cannot be fooled by a local maximum.
    """
    n = len(profile)
    if period <= 0 or n == 0:
        return 0.0

    best_offset, best_score = 0.0, -1.0
    steps = max(1, int(period * 4))
    for step in range(steps):
        offset = step * period / steps
        total, count = 0.0, 0
        position = offset
        while position < n:
            total += profile[int(position)]
            count += 1
            position += period
        score = total / count if count else 0.0
        if score > best_score:
            best_offset, best_score = offset, score
    return best_offset


def _line_contrast(profile: list[float], period: float, offset: float) -> float:
    """How much stronger the edges on the lines are than the map's average.

    The test that separates a grid from a texture. Autocorrelation only asks
    "does this repeat"; a tiled dirt texture repeats perfectly and has no lines
    in it. This asks "is there actually something drawn there", and the answer
    for a texture is 1.0 -- the same as everywhere else.
    """
    if not profile or period <= 0:
        return 0.0

    mean = sum(profile) / len(profile)
    if mean <= 0:
        return 0.0

    total, count, position = 0.0, 0, offset
    while position < len(profile):
        total += profile[int(position)]
        count += 1
        position += period
    return (total / count) / mean if count else 0.0


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def detect(source: Path | Image.Image) -> GridGuess | None:
    """Read the grid off a battlemap, or return None if it cannot be read.

    Returns geometry in *source image* pixels, whatever resolution the detection
    itself ran at.
    """
    try:
        image = Image.open(source) if isinstance(source, Path) else source
        with image:
            return _detect(image)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log.warning("Could not read a grid from %s: %s", source, exc)
        return None


def _detect(image: Image.Image) -> GridGuess | None:
    width, height = image.size
    if width < 200 or height < 200:
        return None

    scale = min(1.0, MAX_EDGE / max(width, height))
    if scale < 1.0:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.BILINEAR,
        )

    columns, rows = _edge_profiles(image)
    x_period, x_score = _spacing(columns)
    y_period, y_score = _spacing(rows)

    period, repetition = _combine(x_period, x_score, y_period, y_score)
    if period <= 0:
        return None

    phase_x = _phase(columns, period)
    phase_y = _phase(rows, period)

    # Both axes must show real lines, not just repetition. Whichever is weaker
    # decides, because a grid drawn on only one axis is not a grid.
    contrast = min(
        _line_contrast(columns, period, phase_x),
        _line_contrast(rows, period, phase_y),
    )
    confidence = repetition * _contrast_factor(contrast)

    if confidence < MIN_CONFIDENCE:
        log.info(
            "No convincing grid found: spacing %.1f repeats at %.2f but its "
            "lines are only %.2fx the average edge (confidence %.2f)",
            period, repetition, contrast, confidence,
        )
        return None

    # The profile index i is the gap between working pixels i and i+1, so the
    # edge it reports sits half a pixel further along than the index suggests.
    offset_x = (phase_x + 0.5) / scale
    offset_y = (phase_y + 0.5) / scale
    size = period / scale

    return GridGuess(
        size_px=round(size, 2),
        offset_x=round(_nearest_offset(offset_x, size), 2),
        offset_y=round(_nearest_offset(offset_y, size), 2),
        confidence=round(confidence, 3),
    )


def _contrast_factor(contrast: float) -> float:
    """Turn a contrast ratio into a multiplier between 0 and 1."""
    span = CONTRAST_FULL - CONTRAST_FLOOR
    return max(0.0, min(1.0, (contrast - CONTRAST_FLOOR) / span))


def _nearest_offset(offset: float, size: float) -> float:
    """The equivalent offset closest to zero.

    An offset of 49.5 on a 50px grid draws the same lines as -0.5, but it reads
    as "the grid is nearly a whole square out" when it is half a pixel out. The
    GM sees this number on a slider.
    """
    offset %= size
    return offset - size if offset > size - 1 else offset


def _combine(
    x_period: float, x_score: float, y_period: float, y_score: float
) -> tuple[float, float]:
    """One spacing from two axes, and how much to believe it.

    Squares are square, so two axes agreeing is the strongest evidence there is
    that what was found is a grid rather than floorboards. Disagreement is not
    fatal -- a map can be cropped mid-square on one axis, or panelled along one
    wall -- but it halves the confidence, which is usually enough to fall below
    the threshold and stay quiet.
    """
    if x_period <= 0 and y_period <= 0:
        return 0.0, 0.0
    if x_period <= 0:
        return y_period, y_score * 0.5
    if y_period <= 0:
        return x_period, x_score * 0.5

    difference = abs(x_period - y_period) / max(x_period, y_period)
    if difference <= AXIS_TOLERANCE:
        total = x_score + y_score
        if total <= 0:
            return 0.0, 0.0
        # Weighted towards the axis that repeats more convincingly.
        period = (x_period * x_score + y_period * y_score) / total
        agreement = 1.0 - difference / AXIS_TOLERANCE * 0.2
        return period, min(1.0, (x_score + y_score) / 2 * (1.0 + agreement) / 1.2)

    if x_score >= y_score:
        return x_period, x_score * 0.5
    return y_period, y_score * 0.5
