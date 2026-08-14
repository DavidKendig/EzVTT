"""Upload validation and path safety.

Uploads are the largest untrusted-input surface in EzVTT, and served media is
the place a traversal attempt would land. Both are tested against real image
bytes rather than mocks.
"""

import io

import pytest
from PIL import Image

from ezvtt import media
from ezvtt.media import MediaError


def png_bytes(width=64, height=48, colour=(120, 90, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "PNG")
    return buffer.getvalue()


def jpeg_bytes(width=32, height=32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_valid_png_is_accepted(tmp_path):
    stored = media.store_upload(png_bytes(200, 100), "Tavern Map.png", tmp_path)
    assert stored.width == 200
    assert stored.height == 100
    assert stored.format == "PNG"
    assert (tmp_path / stored.filename).is_file()


def test_jpeg_is_accepted_and_gets_the_right_extension(tmp_path):
    stored = media.store_upload(jpeg_bytes(), "photo.jpeg", tmp_path)
    assert stored.filename.endswith(".jpg")


def test_extension_does_not_decide_the_format(tmp_path):
    """A JPEG named .png must be stored as the JPEG it actually is."""
    stored = media.store_upload(jpeg_bytes(), "lies.png", tmp_path)
    assert stored.format == "JPEG"
    assert stored.filename.endswith(".jpg")


def test_non_image_is_rejected(tmp_path):
    with pytest.raises(MediaError, match="not an image"):
        media.store_upload(b"this is plain text, not an image", "evil.png", tmp_path)


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(MediaError, match="empty"):
        media.store_upload(b"", "nothing.png", tmp_path)


def test_truncated_image_is_rejected(tmp_path):
    """A half-uploaded map must fail, not become a corrupt battlemap."""
    data = png_bytes(200, 200)
    with pytest.raises(MediaError):
        media.store_upload(data[: len(data) // 3], "cut.png", tmp_path)


def test_oversized_file_is_rejected(tmp_path):
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (media.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(MediaError, match="limit"):
        media.store_upload(huge, "huge.png", tmp_path)


def test_nothing_is_written_when_validation_fails(tmp_path):
    with pytest.raises(MediaError):
        media.store_upload(b"not an image", "bad.png", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_no_partial_files_are_left_behind(tmp_path):
    media.store_upload(png_bytes(), "map.png", tmp_path)
    # The staging file is written under a dotted name and moved into place.
    assert not any(p.name.startswith(".") for p in tmp_path.iterdir())


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hostile", [
    "../../../etc/passwd.png",
    "..\\..\\windows\\system32\\config.png",
    "/absolute/path.png",
    "C:\\Windows\\evil.png",
    "....//....//escape.png",
])
def test_hostile_upload_filenames_cannot_escape(tmp_path, hostile):
    """The client's filename never reaches the filesystem."""
    stored = media.store_upload(png_bytes(), hostile, tmp_path)
    written = tmp_path / stored.filename
    assert written.parent.resolve() == tmp_path.resolve()
    assert "/" not in stored.filename
    assert "\\" not in stored.filename
    assert ".." not in stored.filename


def test_same_name_twice_does_not_overwrite(tmp_path):
    """Two maps called tavern.png must both survive."""
    first = media.store_upload(png_bytes(10, 10), "tavern.png", tmp_path)
    second = media.store_upload(png_bytes(20, 20), "tavern.png", tmp_path)
    assert first.filename != second.filename
    assert (tmp_path / first.filename).is_file()
    assert (tmp_path / second.filename).is_file()


def test_unicode_filenames_produce_a_usable_slug(tmp_path):
    stored = media.store_upload(png_bytes(), "Château d'Été 🏰.png", tmp_path)
    assert stored.filename.endswith(".png")
    assert stored.filename.strip("-.").strip()


@pytest.mark.parametrize("name, expected", [
    ("Tavern Map.png", "Tavern Map"),
    ("goblin_ambush_v2.jpg", "goblin ambush v2"),
])
def test_display_title(name, expected):
    assert media.display_title(name) == expected


def test_display_title_never_empty():
    assert media.display_title(".png").strip()
    assert media.display_title("").strip()


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("attempt", [
    "../secret.db",
    "../../etc/passwd",
    "..\\..\\evil",
    "/etc/passwd",
    "subdir/../../escape",
    "",
    "\x00null",
])
def test_resolve_within_refuses_escapes(tmp_path, attempt):
    with pytest.raises(MediaError):
        media.resolve_within(tmp_path, attempt)


def test_resolve_within_allows_a_plain_name(tmp_path):
    (tmp_path / "map.png").write_bytes(png_bytes())
    resolved = media.resolve_within(tmp_path, "map.png")
    assert resolved == (tmp_path / "map.png").resolve()


def test_unknown_media_kind_is_refused():
    with pytest.raises(MediaError, match="Unknown media kind"):
        media.media_root("../../etc")
    with pytest.raises(MediaError):
        media.media_root("secrets")


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #

def test_thumbnail_is_generated_and_bounded(tmp_path):
    source = tmp_path / "big.png"
    source.write_bytes(png_bytes(1200, 900))

    thumb = media.ensure_thumbnail(source)
    assert thumb is not None and thumb.is_file()

    with Image.open(thumb) as image:
        assert image.width <= media.THUMB_MAX[0]
        assert image.height <= media.THUMB_MAX[1]


def test_thumbnails_never_land_beside_the_source(tmp_path):
    """Bundled Cartos art is read-only under its licence -- ADR-005."""
    source = tmp_path / "bundled_art.png"
    source.write_bytes(png_bytes())

    thumb = media.ensure_thumbnail(source)
    assert thumb is not None
    assert thumb.parent != source.parent
    assert thumb.parent.name == "thumbs"
    # The source directory gained nothing.
    assert [p.name for p in tmp_path.iterdir()] == ["bundled_art.png"]


def test_thumbnail_names_do_not_collide_across_directories(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "same.png").write_bytes(png_bytes(10, 10))
    (b / "same.png").write_bytes(png_bytes(20, 20))

    assert media.thumbnail_path(a / "same.png") != media.thumbnail_path(b / "same.png")


def test_missing_source_yields_no_thumbnail(tmp_path):
    assert media.ensure_thumbnail(tmp_path / "nope.png") is None
