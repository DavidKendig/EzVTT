"""Dice notation, parsed and evaluated on the server.

The client sends notation, never a result. That is the whole point: if the
browser reported the number, a modified client would roll twenty every time and
nobody at the table could tell. See ADR-004.

Supported::

    d20                 one twenty-sided die
    2d6                 two six-sided dice
    2d6+3               ...with a modifier
    1d8+2d6-1           several terms
    2d20kh1             advantage -- keep the highest one
    2d20kl1             disadvantage -- keep the lowest one
    4d6dl1              drop the lowest, the classic stat roll
    d%                  percentile, the same as d100

Everything is bounded. An unbounded ``NdM`` is a denial-of-service against the
server: nobody needs 10000d10000, and refusing it costs nothing.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

MAX_DICE = 100
MAX_SIDES = 1000
MAX_TERMS = 20
MAX_NOTATION_LENGTH = 100

# One term: an optional sign, then either NdM with an optional keep/drop
# suffix, or a plain integer.
_TERM_RE = re.compile(
    r"""
    (?P<sign>[+-])?\s*
    (?:
        (?P<count>\d*)\s*[dD]\s*(?P<sides>\d+|%)     # NdM or d%
        (?:\s*(?P<mode>kh|kl|dh|dl)\s*(?P<keep>\d+)?)?
      |
        (?P<flat>\d+)                                # a bare modifier
    )
    """,
    re.VERBOSE,
)


class DiceError(ValueError):
    """Invalid notation. The message is safe to show the user."""


@dataclass
class Die:
    """One rolled die, and whether it counted toward the total."""

    value: int
    kept: bool = True


@dataclass
class Term:
    notation: str
    sides: int
    dice: list[Die] = field(default_factory=list)
    flat: int = 0
    sign: int = 1

    @property
    def subtotal(self) -> int:
        if self.flat:
            return self.sign * self.flat
        return self.sign * sum(d.value for d in self.dice if d.kept)


@dataclass
class Roll:
    notation: str
    total: int
    terms: list[Term]

    def to_dict(self) -> dict:
        return {
            "notation": self.notation,
            "total": self.total,
            "terms": [
                {
                    "notation": term.notation,
                    "sides": term.sides,
                    "sign": term.sign,
                    "flat": term.flat,
                    "dice": [{"value": d.value, "kept": d.kept} for d in term.dice],
                    "subtotal": term.subtotal,
                }
                for term in self.terms
            ],
        }

    @property
    def summary(self) -> str:
        """A one-line breakdown, e.g. ``[7, 3] + 2``. Dropped dice in brackets."""
        parts: list[str] = []
        for index, term in enumerate(self.terms):
            sign = "-" if term.sign < 0 else ("+" if index else "")
            if term.flat:
                parts.append(f"{sign} {term.flat}".strip())
                continue
            shown = ", ".join(
                str(d.value) if d.kept else f"({d.value})" for d in term.dice
            )
            parts.append(f"{sign} [{shown}]".strip())
        return " ".join(parts)


def _roll_die(sides: int) -> int:
    """A single die.

    ``secrets`` rather than ``random``: the sequence backing ``random`` is
    predictable from a handful of observed outputs, and a player who can predict
    the GM's next roll has a rather large advantage.
    """
    return secrets.randbelow(sides) + 1


def evaluate(notation: str) -> Roll:
    """Parse and roll dice notation. Raises DiceError on anything invalid."""
    original = (notation or "").strip()
    if not original:
        raise DiceError("Type something to roll, like d20 or 2d6+3.")
    if len(original) > MAX_NOTATION_LENGTH:
        raise DiceError("That notation is too long.")

    cleaned = original.replace(" ", "")
    terms: list[Term] = []
    position = 0

    while position < len(cleaned):
        match = _TERM_RE.match(cleaned, position)
        if match is None or match.end() == position:
            raise DiceError(f"Could not understand {original!r}. Try 2d6+3.")
        position = match.end()

        if len(terms) >= MAX_TERMS:
            raise DiceError(f"Too many parts -- {MAX_TERMS} at most.")

        sign = -1 if match.group("sign") == "-" else 1

        if match.group("flat") is not None:
            terms.append(Term(notation=match.group(0), sides=0,
                              flat=int(match.group("flat")), sign=sign))
            continue

        raw_sides = match.group("sides")
        sides = 100 if raw_sides == "%" else int(raw_sides)
        count = int(match.group("count") or 1)

        if not 1 <= count <= MAX_DICE:
            raise DiceError(f"Roll between 1 and {MAX_DICE} dice at a time.")
        if not 2 <= sides <= MAX_SIDES:
            raise DiceError(f"Dice need between 2 and {MAX_SIDES} sides.")

        term = Term(notation=match.group(0), sides=sides, sign=sign)
        term.dice = [Die(_roll_die(sides)) for _ in range(count)]

        mode = match.group("mode")
        if mode:
            keep = int(match.group("keep") or 1)
            _apply_keep_drop(term, mode, keep)

        terms.append(term)

    if not terms:
        raise DiceError(f"Could not understand {original!r}. Try 2d6+3.")

    return Roll(original, sum(term.subtotal for term in terms), terms)


def _apply_keep_drop(term: Term, mode: str, keep: int) -> None:
    """Mark dice as dropped for kh/kl/dh/dl.

    Dropped dice stay in the result rather than being discarded, so the client
    can show the whole roll with the ignored dice greyed out -- which is what
    makes advantage legible at a glance.
    """
    count = len(term.dice)
    if not 1 <= keep <= count:
        raise DiceError(f"Cannot keep or drop {keep} of {count} dice.")

    # Sort indices by value; ties resolve by position so the result is stable.
    order = sorted(range(count), key=lambda i: (term.dice[i].value, i))

    if mode == "kh":
        kept = set(order[-keep:])
    elif mode == "kl":
        kept = set(order[:keep])
    elif mode == "dh":
        kept = set(order[: count - keep])
    else:  # dl
        kept = set(order[keep:])

    for index, die in enumerate(term.dice):
        die.kept = index in kept


# Buttons offered in the chat panel. Ordinary polyhedrals plus the two rolls a
# 5e table makes constantly.
QUICK_ROLLS = ("d4", "d6", "d8", "d10", "d12", "d20", "d100", "2d20kh1", "2d20kl1")
