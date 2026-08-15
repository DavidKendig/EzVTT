"""Pings: "no, the *other* door."

The rules live here rather than in the hub for the same reason chat's audience
rules do -- they are the whole feature, and they are worth being able to test
without a socket.

A ping is the third kind of thing this program draws on a board, and the three
are deliberately different:

  ruler      local      nobody else sees it, nothing is stored
  ping       broadcast  everyone sees it, nothing is stored
  template   broadcast  everyone sees it, and it is still there tomorrow

A ping is a gesture at a shared screen. Storing it would mean deciding when it
expires and reconciling that across five clients, for a mark that has already
served its purpose by the time anyone asks. See ADR-014.
"""

from __future__ import annotations

# One ping a second, per person. Fast enough to jab at the same door twice for
# emphasis; slow enough that a stuck key cannot strobe five screens at once.
MIN_INTERVAL_SECONDS = 1.0

# Grid units. A ping far outside any battlemap is a bug or a bored player, and
# either way it is not worth putting on four other screens.
MAX_COORDINATE = 10_000.0


def allowed(last_at: float | None, now: float) -> bool:
    """Whether this person may ping again yet."""
    return last_at is None or now - last_at >= MIN_INTERVAL_SECONDS


def clean_point(x: object, y: object) -> tuple[float, float]:
    """Validate a ping's coordinates, or raise.

    ``float("nan")`` survives JSON, compares false against every bound, and
    would be drawn at no position at all on every client that received it.
    """
    try:
        px, py = float(x), float(y)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("A ping needs a position.") from exc

    if not (px == px and py == py):                       # NaN
        raise ValueError("A ping needs a position.")
    if abs(px) > MAX_COORDINATE or abs(py) > MAX_COORDINATE:
        raise ValueError("That is not a place on the map.")
    return px, py


def visible_to(x: float, y: float, is_gm: bool, fog_state: dict | None) -> bool:
    """Whether one audience should be shown a ping at this spot.

    A GM sees every ping; the display window authenticates as one, and it is
    showing the real map with the fog drawn over it, so a marker in a concealed
    corridor is fine there.

    A player is shown only what stands on ground they have revealed. A ping in
    the dark would otherwise be the GM pointing at something the fog exists to
    keep from them -- and the same rule applied to tokens and templates before
    it. Fail closed when there is no fog state, as those do. See ADR-011.
    """
    if is_gm:
        return True
    if fog_state is None:
        return False

    from . import fog as fog_module

    return fog_module.is_revealed(fog_state, x, y)
