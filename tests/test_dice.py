"""Dice notation and evaluation.

Rolls happen on the server so a modified client cannot fake one. That makes the
evaluator's correctness the only thing standing between the table and a player
who rolls twenty every time, so it is tested closely.
"""

import pytest

from ezvtt.dice import MAX_DICE, MAX_SIDES, DiceError, evaluate

# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("notation, dice, sides", [
    ("d20", 1, 20),
    ("D20", 1, 20),
    ("1d20", 1, 20),
    ("2d6", 2, 6),
    ("10d10", 10, 10),
    ("d%", 1, 100),
    (" 2d6 ", 2, 6),
])
def test_simple_notation(notation, dice, sides):
    roll = evaluate(notation)
    assert len(roll.terms[0].dice) == dice
    assert roll.terms[0].sides == sides


def test_modifier_is_added(monkeypatch):
    monkeypatch.setattr("ezvtt.dice._roll_die", lambda sides: 4)
    assert evaluate("2d6+3").total == 11
    assert evaluate("2d6-3").total == 5


def test_several_terms(monkeypatch):
    monkeypatch.setattr("ezvtt.dice._roll_die", lambda sides: 2)
    # 2 + (2+2) - 1
    assert evaluate("1d8+2d6-1").total == 5


def test_bare_modifier_alone():
    assert evaluate("5").total == 5


@pytest.mark.parametrize("bad", [
    "", "   ", "hello", "d", "2d", "+", "d20+", "2d6++3", "abc123", "2x6",
])
def test_nonsense_is_refused(bad):
    with pytest.raises(DiceError):
        evaluate(bad)


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["0d6", f"{MAX_DICE + 1}d6", "9999d6"])
def test_dice_count_is_bounded(bad):
    """An unbounded NdM is a denial-of-service against the server."""
    with pytest.raises(DiceError, match="between 1 and"):
        evaluate(bad)


@pytest.mark.parametrize("bad", ["d0", "d1", f"d{MAX_SIDES + 1}", "d999999"])
def test_side_count_is_bounded(bad):
    with pytest.raises(DiceError, match="sides"):
        evaluate(bad)


def test_notation_length_is_bounded():
    with pytest.raises(DiceError, match="too long"):
        evaluate("1d6" + "+1d6" * 40)


def test_term_count_is_bounded():
    with pytest.raises(DiceError, match="too long|Too many"):
        evaluate("+".join(["1"] * 60))


def test_the_maximum_is_actually_allowed():
    roll = evaluate(f"{MAX_DICE}d{MAX_SIDES}")
    assert len(roll.terms[0].dice) == MAX_DICE


# --------------------------------------------------------------------------- #
# Keep and drop
# --------------------------------------------------------------------------- #

def test_advantage_keeps_the_highest():
    values = iter([7, 19])
    roll = _with_values(values, "2d20kh1")
    assert roll.total == 19
    assert [d.kept for d in roll.terms[0].dice] == [False, True]


def test_disadvantage_keeps_the_lowest():
    roll = _with_values(iter([7, 19]), "2d20kl1")
    assert roll.total == 7
    assert [d.kept for d in roll.terms[0].dice] == [True, False]


def test_drop_lowest_is_the_classic_stat_roll():
    roll = _with_values(iter([6, 5, 4, 1]), "4d6dl1")
    assert roll.total == 15
    assert [d.kept for d in roll.terms[0].dice] == [True, True, True, False]


def test_drop_highest():
    roll = _with_values(iter([6, 5, 4, 1]), "4d6dh1")
    assert roll.total == 10


def test_keep_more_than_one():
    roll = _with_values(iter([1, 2, 3, 4]), "4d6kh2")
    assert roll.total == 7


def test_dropped_dice_are_reported_not_discarded():
    """The client greys them out, which is what makes advantage legible."""
    roll = _with_values(iter([7, 19]), "2d20kh1")
    assert len(roll.terms[0].dice) == 2


@pytest.mark.parametrize("bad", ["2d20kh5", "1d6dl2", "2d6kh0"])
def test_keeping_more_dice_than_were_rolled_is_refused(bad):
    with pytest.raises(DiceError, match="keep or drop"):
        evaluate(bad)


def _with_values(values, notation):
    import ezvtt.dice as dice_module

    original = dice_module._roll_die
    dice_module._roll_die = lambda sides: next(values)
    try:
        return evaluate(notation)
    finally:
        dice_module._roll_die = original


# --------------------------------------------------------------------------- #
# Behaviour of the real generator
# --------------------------------------------------------------------------- #

def test_results_stay_within_range():
    for _ in range(300):
        roll = evaluate("3d6")
        for die in roll.terms[0].dice:
            assert 1 <= die.value <= 6
        assert 3 <= roll.total <= 18


def test_every_face_comes_up():
    """A generator stuck on a subset of faces would be a silent disaster."""
    seen = {evaluate("d6").terms[0].dice[0].value for _ in range(400)}
    assert seen == {1, 2, 3, 4, 5, 6}


def test_the_distribution_is_not_obviously_skewed():
    rolls = [evaluate("d20").terms[0].dice[0].value for _ in range(4000)]
    mean = sum(rolls) / len(rolls)
    # A fair d20 averages 10.5. This is wide enough not to flake and tight
    # enough to catch an off-by-one or a truncated range.
    assert 9.5 < mean < 11.5, f"mean {mean}"


def test_rolls_differ_from_each_other():
    assert len({evaluate("d100").total for _ in range(50)}) > 10


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #

def test_summary_shows_dropped_dice_in_brackets():
    roll = _with_values(iter([7, 19]), "2d20kh1")
    assert "(7)" in roll.summary
    assert "19" in roll.summary


def test_to_dict_round_trips_the_detail():
    roll = _with_values(iter([3, 5]), "2d6+1")
    data = roll.to_dict()
    assert data["notation"] == "2d6+1"
    assert data["total"] == 9
    assert [d["value"] for d in data["terms"][0]["dice"]] == [3, 5]
    assert data["terms"][1]["flat"] == 1


def test_negative_term_is_signed_in_the_detail():
    roll = _with_values(iter([4]), "1d6-2")
    assert roll.to_dict()["terms"][1]["sign"] == -1
    assert roll.total == 2
