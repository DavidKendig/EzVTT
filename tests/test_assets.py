"""Asset library: categorisation, import, and search."""

import io

import pytest
from PIL import Image

from ezvtt import assets, config, db


@pytest.fixture
def library(tmp_path, monkeypatch):
    db_path = tmp_path / "assets.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.close()
    db.migrate(db_path)
    yield tmp_path
    db.close()


def write_png(directory, name, size=(32, 32)):
    directory.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", size, (100, 80, 60)).save(buffer, "PNG")
    (directory / name).write_bytes(buffer.getvalue())


# --------------------------------------------------------------------------- #
# Categorisation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name, expected", [
    ("Ale Barrel 02", "Food & drink"),
    ("Barrel Empty Top", "Containers"),
    ("Arcane Circle 01", "Magic"),
    ("Bear Trap", "Traps"),
    ("Farmers Scythe", "Farming & animals"),
    ("Farmers Pichfork", "Farming & animals"),   # the bundle misspells it
    ("Torturers Cage 01", "Macabre"),
    ("Wooden Walkway 04", "Structures"),
    ("Standing Candelabra Bronze", "Lighting"),
    ("Grandfather Clock", "Decor"),
    ("Sampan 01", "Nautical"),
])
def test_categorise(name, expected):
    assert assets.categorise(name) == expected


def test_torture_cage_is_macabre_not_a_container():
    """Rule order matters: Macabre is checked before Containers."""
    assert assets.categorise("Torturers Cage 01") == "Macabre"
    assert assets.categorise("Storage Crate") == "Containers"


def test_unknown_names_fall_back():
    assert assets.categorise("Zzyzx Widget") == assets.FALLBACK_CATEGORY


def test_categorise_is_case_insensitive():
    assert assets.categorise("ALE BARREL") == assets.categorise("ale barrel")


@pytest.mark.skipif(
    not config.BUNDLED_ASSETS_DIR.is_dir()
    or not any(config.BUNDLED_ASSETS_DIR.glob("*.png")),
    reason="Tom Cartos bundle not installed",
)
def test_real_bundle_is_almost_entirely_categorised():
    """A library that is mostly "Other" is a library nobody can browse."""
    from ezvtt.grid import display_name

    names = [display_name(p.name) for p in config.BUNDLED_ASSETS_DIR.glob("*.png")]
    other = sum(1 for n in names if assets.categorise(n) == assets.FALLBACK_CATEGORY)
    assert other / len(names) < 0.05, f"{other}/{len(names)} uncategorised"


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def test_import_reads_footprint_and_name(library, monkeypatch):
    bundle = library / "bundled"
    write_png(bundle, "TC_Anvil 02_2x1.png")
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)

    assert assets.import_bundled(bundle)["added"] == 1

    found = assets.search("Anvil")["assets"][0]
    assert found["name"] == "Anvil 02"
    assert (found["grid_w"], found["grid_h"]) == (2, 1)
    assert found["source"] == "bundled"


def test_import_is_idempotent(library, monkeypatch):
    bundle = library / "bundled"
    write_png(bundle, "TC_Barrel_1x1.png")
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)

    assert assets.import_bundled(bundle)["added"] == 1
    assert assets.import_bundled(bundle)["added"] == 0
    assert assets.search("")["total"] == 1


def test_import_picks_up_newly_added_files(library, monkeypatch):
    bundle = library / "bundled"
    write_png(bundle, "TC_First_1x1.png")
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)
    assets.import_bundled(bundle)

    write_png(bundle, "TC_Second_2x2.png")
    assert assets.import_bundled(bundle)["added"] == 1
    assert assets.search("")["total"] == 2


def test_import_never_writes_into_the_bundle(library, monkeypatch):
    """Tom's Open Map License forbids editing these files. ADR-005."""
    bundle = library / "bundled"
    write_png(bundle, "TC_Chair_1x1.png")
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)

    before = {p.name: p.stat().st_mtime_ns for p in bundle.iterdir()}
    assets.import_bundled(bundle)
    after = {p.name: p.stat().st_mtime_ns for p in bundle.iterdir()}
    assert before == after


def test_import_of_a_missing_directory_is_harmless(library):
    assert assets.import_bundled(library / "nope")["total"] == 0


def test_non_images_are_ignored(library, monkeypatch):
    bundle = library / "bundled"
    write_png(bundle, "TC_Real_1x1.png")
    (bundle / "readme.txt").write_text("not art", encoding="utf-8")
    (bundle / "LICENSE-ASSETS.md").write_text("licence", encoding="utf-8")
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)

    assert assets.import_bundled(bundle)["added"] == 1


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

@pytest.fixture
def stocked(library, monkeypatch):
    bundle = library / "bundled"
    for name in ("TC_Ale Barrel_1x1.png", "TC_Oak Table_2x1.png",
                 "TC_Arcane Circle_3x3.png", "TC_Bear Trap_1x1.png"):
        write_png(bundle, name)
    monkeypatch.setattr(config, "BUNDLED_ASSETS_DIR", bundle)
    assets.import_bundled(bundle)
    return bundle


def test_search_matches_substrings_case_insensitively(stocked):
    assert assets.search("barrel")["total"] == 1
    assert assets.search("BARREL")["total"] == 1
    assert assets.search("ale")["total"] == 1


def test_search_filters_by_category(stocked):
    assert assets.search("", "Traps")["total"] == 1
    assert assets.search("", "Magic")["total"] == 1
    assert assets.search("", "Nautical")["total"] == 0


@pytest.mark.parametrize("wildcard", ["%", "_", "%%", "a%", "\\"])
def test_like_wildcards_are_escaped(stocked, wildcard):
    """A search for "%" must match names containing %, not everything."""
    assert assets.search(wildcard)["total"] == 0


def test_search_pages(stocked):
    first = assets.search("", limit=2, offset=0)
    second = assets.search("", limit=2, offset=2)
    assert first["total"] == 4
    assert len(first["assets"]) == 2
    assert len(second["assets"]) == 2
    assert {a["id"] for a in first["assets"]}.isdisjoint(a["id"] for a in second["assets"])


def test_search_limit_is_bounded(stocked):
    """An unbounded limit is a denial-of-service against the GM's own browser."""
    assert len(assets.search("", limit=100_000)["assets"]) <= 500


def test_category_counts(stocked):
    counts = {row["category"]: row["count"] for row in assets.category_counts()}
    assert counts["Traps"] == 1
    assert sum(counts.values()) == 4


# --------------------------------------------------------------------------- #
# Mutation
# --------------------------------------------------------------------------- #

def test_resize_is_metadata_only_and_clamped(stocked):
    asset_id = assets.search("Barrel")["assets"][0]["id"]
    before = (stocked / "TC_Ale Barrel_1x1.png").read_bytes()

    assets.resize_asset(asset_id, 3, 2)
    assert (assets.get_asset(asset_id)["grid_w"],
            assets.get_asset(asset_id)["grid_h"]) == (3, 2)

    assets.resize_asset(asset_id, 9999, -5)
    resized = assets.get_asset(asset_id)
    assert resized["grid_w"] == assets.get_asset(asset_id)["grid_w"] <= 40
    assert resized["grid_h"] >= 0.1

    # The file on disk is untouched. ADR-005.
    assert (stocked / "TC_Ale Barrel_1x1.png").read_bytes() == before


def test_bundled_assets_cannot_be_deleted(stocked):
    asset_id = assets.search("Barrel")["assets"][0]["id"]
    with pytest.raises(ValueError, match="Bundled artwork"):
        assets.delete_asset(asset_id)
    assert assets.get_asset(asset_id) is not None


def test_rename(stocked):
    asset_id = assets.search("Barrel")["assets"][0]["id"]
    assets.rename_asset(asset_id, "Keg of Ale")
    assert assets.get_asset(asset_id)["name"] == "Keg of Ale"


def test_rename_rejects_blank(stocked):
    asset_id = assets.search("Barrel")["assets"][0]["id"]
    with pytest.raises(ValueError):
        assets.rename_asset(asset_id, "   ")
