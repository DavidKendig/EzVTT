"""The initiative tracker: order, turns, rounds, and who is told about them.

Two rules carry the feature. The tracker must keep running when the creature
whose turn it is dies -- that is the most ordinary thing that can happen to it,
and a pointer left dangling would stall the fight. And a concealed entry must be
absent from a player's payload rather than flagged in it: a GM rolls the ambush
into the order before the party knows there is one. See ADR-004.
"""

import io

import pytest
from PIL import Image

from ezvtt import config, db, initiative, media, state


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A scene on the table, with an asset to make creatures out of."""
    db_path = tmp_path / "initiative.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "MAPS_DIR", tmp_path / "maps")
    monkeypatch.setattr(config, "FOG_DIR", tmp_path / "fog")
    db.close()
    db.migrate(db_path)

    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1000), (60, 50, 40)).save(buffer, "PNG")
    stored = media.store_upload(buffer.getvalue(), "Tavern.png", config.MAPS_DIR)
    map_id = state.create_map(stored, "Tavern")
    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    conn = db.connect()
    cursor = conn.execute(
        """INSERT INTO assets (name, filename, source, grid_w, grid_h)
           VALUES ('Goblin', 'goblin.png', 'bundled', 1, 1)"""
    )
    conn.commit()

    yield {"scene_id": scene_id, "asset_id": cursor.lastrowid, "map_id": map_id}
    db.close()


def order_of(scene_id) -> list[str]:
    return [e["label"] for e in initiative.get(scene_id)["entries"]]


def current_label(scene_id) -> str | None:
    tracker = initiative.get(scene_id)
    return next(
        (e["label"] for e in tracker["entries"] if e["id"] == tracker["current_id"]),
        None,
    )


# --------------------------------------------------------------------------- #
# Order
# --------------------------------------------------------------------------- #

def test_highest_acts_first(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)
    initiative.add(scene, "Brand", value=15)

    assert order_of(scene) == ["Goblin", "Brand", "Anya"]


def test_a_tie_is_broken_by_the_higher_modifier(table):
    """The way the rules break it, rather than by whoever was typed in first."""
    scene = table["scene_id"]
    initiative.add(scene, "Slow", value=15, modifier=0)
    initiative.add(scene, "Quick", value=15, modifier=4)

    assert order_of(scene) == ["Quick", "Slow"]


def test_an_exact_tie_is_stable(table):
    """Two identical goblins must not shuffle between reads."""
    scene = table["scene_id"]
    initiative.add(scene, "Goblin", value=15, modifier=2)
    initiative.add(scene, "Goblin", value=15, modifier=2)

    assert order_of(scene) == order_of(scene) == ["Goblin", "Goblin 2"]


def test_identical_names_are_numbered(table):
    """Three rows reading "Goblin" are useless for the one thing this does."""
    scene = table["scene_id"]
    for _ in range(3):
        initiative.add(scene, "Goblin")

    assert order_of(scene) == ["Goblin", "Goblin 2", "Goblin 3"]


def test_the_order_is_capped(table):
    scene = table["scene_id"]
    for i in range(initiative.MAX_ENTRIES):
        initiative.add(scene, f"Body {i}")

    with pytest.raises(ValueError, match="at most"):
        initiative.add(scene, "One too many")


def test_adding_to_a_missing_scene_is_refused(table):
    with pytest.raises(ValueError, match="scene"):
        initiative.add(9999, "Nobody")


# --------------------------------------------------------------------------- #
# Adding tokens
# --------------------------------------------------------------------------- #

def test_tokens_join_under_their_own_names(table):
    scene = table["scene_id"]
    first = state.place_token(scene, table["asset_id"], 1, 1, "token")
    second = state.place_token(scene, table["asset_id"], 2, 2, "token")
    state.update_token(second["id"], label="Captain")

    initiative.add_tokens(scene, [first["id"], second["id"]])

    assert sorted(order_of(scene)) == ["Captain", "Goblin"]


def test_a_token_already_in_the_order_is_not_added_twice(table):
    """Clicking Add tokens again after dropping two more must be safe."""
    scene = table["scene_id"]
    token = state.place_token(scene, table["asset_id"], 1, 1, "token")

    initiative.add_tokens(scene, [token["id"]])
    added = initiative.add_tokens(scene, [token["id"]])

    assert added == []
    assert len(initiative.get(scene)["entries"]) == 1


def test_a_concealed_token_joins_concealed(table):
    """Adding the ambush to the order must not be what announces it."""
    scene = table["scene_id"]
    token = state.place_token(scene, table["asset_id"], 8, 8, "token")
    state.update_token(token["id"], hidden=True)

    initiative.add_tokens(scene, [token["id"]])

    assert initiative.get(scene)["entries"][0]["hidden"] is True
    assert initiative.get(scene, for_gm=False)["entries"] == []


def test_a_token_from_another_scene_is_ignored(table):
    other = state.create_scene(table["map_id"], "Elsewhere")
    token = state.place_token(other, table["asset_id"], 1, 1, "token")

    assert initiative.add_tokens(table["scene_id"], [token["id"]]) == []


def test_revealing_a_token_reveals_its_entry(table):
    """Otherwise hiding a token again leaves its name in the players' order."""
    scene = table["scene_id"]
    token = state.place_token(scene, table["asset_id"], 8, 8, "token")
    state.update_token(token["id"], hidden=True)
    initiative.add_tokens(scene, [token["id"]])

    assert initiative.sync_token_visibility(token["id"], hidden=False) == scene
    assert initiative.get(scene, for_gm=False)["entries"][0]["label"] == "Goblin"

    initiative.sync_token_visibility(token["id"], hidden=True)
    assert initiative.get(scene, for_gm=False)["entries"] == []


def test_syncing_a_token_with_no_entry_is_harmless(table):
    token = state.place_token(table["scene_id"], table["asset_id"], 1, 1, "token")
    assert initiative.sync_token_visibility(token["id"], hidden=True) is None


# --------------------------------------------------------------------------- #
# Running the combat
# --------------------------------------------------------------------------- #

def test_starting_puts_the_top_of_the_order_on_round_one(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)

    tracker = initiative.start(scene)

    assert tracker["round"] == 1
    assert current_label(scene) == "Goblin"


def test_starting_an_empty_order_is_refused(table):
    with pytest.raises(ValueError, match="Add someone"):
        initiative.start(table["scene_id"])


def test_the_turn_goes_round_and_the_round_ticks_over(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)

    assert current_label(scene) == "Goblin"
    assert initiative.advance(scene)["round"] == 1
    assert current_label(scene) == "Anya"

    assert initiative.advance(scene)["round"] == 2
    assert current_label(scene) == "Goblin"


def test_the_turn_goes_back(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)
    initiative.advance(scene)
    initiative.advance(scene)              # round 2, Goblin

    assert initiative.advance(scene, -1)["round"] == 1
    assert current_label(scene) == "Anya"


def test_round_one_does_not_go_backwards_into_nothing(table):
    """A misclick at the top of a fight should stay put, not end the combat."""
    scene = table["scene_id"]
    initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)

    tracker = initiative.advance(scene, -1)

    assert tracker["round"] == 1
    assert current_label(scene) == "Goblin"


def test_advancing_before_the_combat_starts_starts_it(table):
    scene = table["scene_id"]
    initiative.add(scene, "Goblin", value=19)

    assert initiative.advance(scene)["round"] == 1


def test_advancing_an_empty_order_is_refused(table):
    with pytest.raises(ValueError, match="nobody"):
        initiative.advance(table["scene_id"])


def test_jumping_hands_the_turn_over_without_moving_the_round(table):
    """"No, we skipped Anya" should not roll the round over on the way back."""
    scene = table["scene_id"]
    anya = initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)
    initiative.advance(scene)
    initiative.advance(scene)              # round 2

    tracker = initiative.jump(scene, anya["id"])

    assert tracker["round"] == 2
    assert current_label(scene) == "Anya"


def test_jumping_before_the_combat_starts_starts_it(table):
    scene = table["scene_id"]
    entry = initiative.add(scene, "Anya", value=12)

    assert initiative.jump(scene, entry["id"])["round"] == 1
    assert current_label(scene) == "Anya"


def test_ending_a_combat_keeps_the_party(table):
    """They are still the party after the fight; retyping them is not a feature."""
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.start(scene)

    tracker = initiative.stop(scene)

    assert tracker["round"] == 0
    assert tracker["current_id"] is None
    assert order_of(scene) == ["Anya"]


def test_clearing_empties_the_order_and_the_round(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya")
    initiative.start(scene)

    assert initiative.clear(scene) == 1
    assert initiative.get(scene) == {"round": 0, "entries": [], "current_id": None}


# --------------------------------------------------------------------------- #
# Losing the creature whose turn it is
# --------------------------------------------------------------------------- #

def test_removing_the_current_entry_hands_the_turn_on(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    goblin = initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)                # Goblin is up

    initiative.remove(goblin["id"])

    assert current_label(scene) == "Anya"
    assert initiative.get(scene)["round"] == 1


def test_removing_the_last_entry_in_the_round_does_not_advance_the_round(table):
    """A creature dying is not the table taking a turn."""
    scene = table["scene_id"]
    initiative.add(scene, "Goblin", value=19)
    anya = initiative.add(scene, "Anya", value=12)
    initiative.start(scene)
    initiative.advance(scene)              # Anya, last in the order

    initiative.remove(anya["id"])

    assert initiative.get(scene)["round"] == 1
    assert current_label(scene) == "Goblin"


def test_a_dead_token_takes_its_entry_and_the_tracker_keeps_running(table):
    """The entry cascades with the token; the pointer must not be left dangling."""
    scene = table["scene_id"]
    token = state.place_token(scene, table["asset_id"], 1, 1, "token")
    initiative.add_tokens(scene, [token["id"]])
    initiative.add(scene, "Anya", value=1)
    initiative.roll(scene, initiative.get(scene)["entries"][0]["id"])
    initiative.start(scene)

    state.delete_token(token["id"])
    tracker = initiative.get(scene)

    assert [e["label"] for e in tracker["entries"]] == ["Anya"]
    assert tracker["current_id"] == tracker["entries"][0]["id"]


def test_removing_a_missing_entry_reports_it(table):
    assert initiative.remove(9999) is False


# --------------------------------------------------------------------------- #
# Rolling
# --------------------------------------------------------------------------- #

def test_rolling_one_entry_leaves_the_others_alone(table):
    scene = table["scene_id"]
    rolled = initiative.add(scene, "Goblin", value=0, modifier=2)
    initiative.add(scene, "Anya", value=7)

    initiative.roll(scene, rolled["id"])
    entries = {e["label"]: e["value"] for e in initiative.get(scene)["entries"]}

    assert entries["Anya"] == 7
    assert 3 <= entries["Goblin"] <= 22        # 1d20 + 2


def test_rolling_everyone_rolls_everyone(table):
    scene = table["scene_id"]
    for i in range(6):
        initiative.add(scene, f"Body {i}", value=0)

    initiative.roll(scene)
    values = [e["value"] for e in initiative.get(scene)["entries"]]

    assert all(1 <= v <= 20 for v in values)
    # Six d20s all landing on the same face is a 1-in-3-million coincidence;
    # every one being *identical* every run would mean they are not being rolled.
    assert len(set(values)) > 1


def test_rolling_a_missing_entry_is_refused(table):
    with pytest.raises(ValueError, match="no longer"):
        initiative.roll(table["scene_id"], 9999)


def test_a_roll_reorders_the_turn_order(table):
    scene = table["scene_id"]
    initiative.add(scene, "Certain", value=999)
    rolled = initiative.add(scene, "Rolled", value=0)

    initiative.roll(scene, rolled["id"])

    assert order_of(scene)[0] == "Certain"


# --------------------------------------------------------------------------- #
# What each audience is told
# --------------------------------------------------------------------------- #

def test_a_concealed_entry_never_reaches_a_player(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Assassin in the rafters", value=19, hidden=True)

    player = initiative.get(scene, for_gm=False)

    assert [e["label"] for e in player["entries"]] == ["Anya"]
    assert "rafters" not in repr(player)


def test_a_player_is_not_told_who_is_acting_when_it_is_concealed(table):
    """The round still moves, so they know the fight is; not who is moving it."""
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Assassin", value=19, hidden=True)
    initiative.start(scene)                # the assassin is up

    player = initiative.get(scene, for_gm=False)

    assert player["round"] == 1
    assert player["current_id"] is None
    assert initiative.get(scene)["current_id"] is not None


def test_the_snapshot_carries_the_tracker_for_both_audiences(table):
    scene = table["scene_id"]
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Assassin", value=19, hidden=True)
    initiative.start(scene)

    gm = state.snapshot(for_gm=True)["initiative"]
    player = state.snapshot(for_gm=False)["initiative"]

    assert len(gm["entries"]) == 2
    assert len(player["entries"]) == 1
    assert "Assassin" not in repr(state.snapshot(for_gm=False))


def test_the_snapshot_is_quiet_with_nothing_on_the_table(table):
    state.delete_map(table["map_id"])
    assert state.snapshot(for_gm=True)["initiative"] == {
        "round": 0, "entries": [], "current_id": None,
    }


def test_a_combat_survives_a_scene_switch(table):
    """Switching away mid-fight and back must return to round four, not to a
    cleared tracker. It is the encounter's combat, not the table's."""
    scene = table["scene_id"]
    other = state.create_scene(table["map_id"], "Elsewhere")
    initiative.add(scene, "Anya", value=12)
    initiative.add(scene, "Goblin", value=19)
    initiative.start(scene)
    initiative.advance(scene)
    initiative.advance(scene)
    initiative.advance(scene)              # round 2, Anya

    state.activate_scene(other)
    assert state.snapshot(for_gm=True)["initiative"]["round"] == 0

    state.activate_scene(scene)
    restored = state.snapshot(for_gm=True)["initiative"]

    assert restored["round"] == 2
    assert current_label(scene) == "Anya"
