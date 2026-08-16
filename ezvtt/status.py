"""Hit points and conditions, and who is told what about them.

The rules live here rather than in ``state`` because they are the feature: what
a player may know about a creature's health is a GM's decision at every table,
and getting it wrong leaks the encounter rather than merely looking untidy.

  conditions   public       a prone goblin is prone in front of everyone
  HP bar       public       coarse, in quarters -- what a glance at a bar tells you
  HP numbers   GM, and the player who owns that token

**The bar is bucketed, not scaled.** Sending a fraction to two decimal places
would let anyone who knows a monster's maximum work out its exact hit points,
which is the thing the numbers were withheld to prevent. Quarters carry what a
bar actually shows and nothing more. See ADR-017.
"""

from __future__ import annotations

from typing import Any

# The 5e condition list, plus the two markers every table improvises anyway.
# A fixed vocabulary rather than free text: these are drawn as badges on a
# canvas, and "whatever the client sent" is not something to render.
# ``short`` is what fits on a badge over a token at table zoom. Two letters,
# all distinct -- "dead" and "deafened" would otherwise both be DE, which is a
# poor thing to be vague about.
CONDITIONS: dict[str, dict[str, str]] = {
    "blinded": {"label": "Blinded", "short": "BL"},
    "charmed": {"label": "Charmed", "short": "CH"},
    "concentrating": {"label": "Concentrating", "short": "CN"},
    "dead": {"label": "Dead", "short": "DD"},
    "deafened": {"label": "Deafened", "short": "DF"},
    "frightened": {"label": "Frightened", "short": "FR"},
    "grappled": {"label": "Grappled", "short": "GR"},
    "incapacitated": {"label": "Incapacitated", "short": "IN"},
    "invisible": {"label": "Invisible", "short": "IV"},
    "paralyzed": {"label": "Paralysed", "short": "PA"},
    "petrified": {"label": "Petrified", "short": "PE"},
    "poisoned": {"label": "Poisoned", "short": "PO"},
    "prone": {"label": "Prone", "short": "PR"},
    "restrained": {"label": "Restrained", "short": "RE"},
    "stunned": {"label": "Stunned", "short": "ST"},
    "unconscious": {"label": "Unconscious", "short": "UN"},
}

# Enough for anything a creature can plausibly be suffering at once, and few
# enough that the badges still fit above the token.
MAX_CONDITIONS = 8

# Hit points are bounded so a typo cannot produce a bar with ten thousand
# segments or a negative maximum to divide by.
MIN_HP, MAX_HP = -9_999, 9_999
MAX_HP_MAX = 9_999

# The bar has this many steps. Four is what a bar reads as at a glance --
# full, most, half, nearly gone -- and it is coarse enough that knowing the
# maximum does not give the exact number back.
BAR_STEPS = 4


def clean_conditions(value: Any) -> str:
    """Normalise a condition list into storage form, dropping anything unknown.

    Accepts a list or a comma-separated string, because the socket carries one
    and the database holds the other.
    """
    if value is None:
        return ""
    parts = value if isinstance(value, list) else str(value).split(",")

    seen: list[str] = []
    for part in parts:
        name = str(part).strip().lower()
        if name in CONDITIONS and name not in seen:
            seen.append(name)
        if len(seen) >= MAX_CONDITIONS:
            break
    return ",".join(seen)


def condition_list(stored: str) -> list[str]:
    return [name for name in (stored or "").split(",") if name in CONDITIONS]


def clean_hp(value: Any) -> int | None:
    """A hit point total, or None for "not tracked"."""
    if value is None or value == "":
        return None
    return max(MIN_HP, min(MAX_HP, int(float(value))))


def clean_hp_max(value: Any) -> int | None:
    if value is None or value == "":
        return None
    # Zero maximum would divide by zero when drawing the bar, and a negative
    # one is not a thing.
    return max(1, min(MAX_HP_MAX, int(float(value))))


def bar_steps(hp: int | None, hp_max: int | None) -> int | None:
    """How full the bar looks, in quarters, or None when nothing is tracked.

    Rounded *up* so that a creature clinging on at one hit point still shows a
    sliver rather than an empty bar -- an empty bar reads as dead, and the
    difference matters to whoever is deciding whether to run.
    """
    if hp is None or hp_max is None or hp_max <= 0:
        return None
    if hp <= 0:
        return 0

    import math

    return max(1, min(BAR_STEPS, math.ceil(hp / hp_max * BAR_STEPS)))


def visible_status(
    row: Any, for_gm: bool, viewer_id: int | None
) -> dict[str, Any]:
    """The health and conditions fields for one audience's copy of a token.

    A GM sees the numbers. So does the player who owns the token: it is their
    character, and hiding their own hit points from them would be absurd.
    Everyone else gets a bar in quarters, and only when the GM has left it
    public -- otherwise nothing at all, which is how a token with no health
    tracked already looks.
    """
    hp, hp_max = row["hp"], row["hp_max"]
    conditions = condition_list(row["conditions"])

    if for_gm or (viewer_id is not None and row["owner_user_id"] == viewer_id):
        return {
            "hp": hp,
            "hp_max": hp_max,
            "hp_public": bool(row["hp_public"]),
            "hp_bar": bar_steps(hp, hp_max),
            "conditions": conditions,
        }

    if not row["hp_public"]:
        return {"conditions": conditions}

    steps = bar_steps(hp, hp_max)
    return {"hp_bar": steps, "conditions": conditions} if steps is not None else {
        "conditions": conditions
    }
