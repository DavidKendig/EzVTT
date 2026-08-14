"""Image upload validation, thumbnails, and safe path resolution.

Uploads are the largest untrusted-input surface in EzVTT: a GM in a hurry drags
a file in, and it is written to disk and then served back to every player. Three
things are therefore checked here rather than trusted.

**Content, not extension.** A file called ``map.png`` may be anything. Pillow
opens and verifies the bytes; whatever the browser claimed the type was is
ignored.

**Decompression bombs.** A few kilobytes of PNG can decode to gigabytes of
pixels. Pillow's own guard is raised to a deliberate ceiling and pixel count is
checked before any full decode.

**Filenames.** The client's filename never reaches the filesystem. A slug is
derived from it for display and a fresh name is generated for storage, so
``../../.ssh/authorized_keys`` and friends have nowhere to go.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import config

# Formats a browser can display and Pillow can read reliably. GIF and BMP are
# accepted for convenience; anything exotic is refused rather than guessed at.
ALLOWED_FORMATS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
}

# A 20000x20000 battlemap is already absurd; this is generous headroom over any
# real map while staying far below the point where decoding exhausts memory.
MAX_PIXELS = 80_000_000
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

# Pillow warns above ~89 megapixels and refuses above twice that. Pin it to our
# own limit so the ceiling is one number rather than two that can disagree.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

THUMB_MAX = (320, 320)

_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACES = re.compile(r"[\s_-]+")


class MediaError(ValueError):
    """An upload was rejected. The message is safe to show the user."""


@dataclass(frozen=True)
class StoredImage:
    filename: str
    width: int
    height: int
    format: str
    size_bytes: int


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

def slugify(value: str, fallback: str = "untitled") -> str:
    """A filesystem- and URL-safe slug. Never empty, never a path."""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = _SLUG_STRIP.sub("", value).strip()
    value = _SLUG_SPACES.sub("-", value).strip("-").lower()
    return value[:60] or fallback


def display_title(filename: str) -> str:
    """A human title from an uploaded filename, for the map's default name."""
    stem = Path(filename).stem
    stem = _SLUG_SPACES.sub(" ", stem).strip()
    return stem[:120] or "Untitled map"


def _storage_name(original: str, extension: str) -> str:
    """Generate the on-disk name.

    The random suffix is not for secrecy -- it prevents two maps called
    "tavern.png" from overwriting one another, which would silently destroy a
    GM's earlier upload.
    """
    return f"{slugify(Path(original).stem, 'map')}-{secrets.token_hex(4)}{extension}"


# --------------------------------------------------------------------------- #
# Validation and storage
# --------------------------------------------------------------------------- #

def _inspect(data: bytes) -> tuple[str, int, int]:
    """Identify an image from its bytes. Returns (format, width, height)."""
    import io

    try:
        # verify() detects truncation and corruption but leaves the image
        # unusable afterwards, so this pass is purely a check.
        with Image.open(io.BytesIO(data)) as probe:
            image_format = probe.format or ""
            width, height = probe.size
            probe.verify()
    except UnidentifiedImageError:
        raise MediaError(
            "That file is not an image EzVTT can read. "
            "PNG, JPEG, WebP, GIF, and BMP are supported."
        ) from None
    except Image.DecompressionBombError:
        raise MediaError(
            f"That image is too large to open safely "
            f"(over {MAX_PIXELS // 1_000_000} megapixels)."
        ) from None
    except Exception:
        raise MediaError("That image appears to be corrupt or incomplete.") from None

    if image_format not in ALLOWED_FORMATS:
        supported = ", ".join(sorted(ALLOWED_FORMATS))
        raise MediaError(f"{image_format or 'That format'} is not supported. Use {supported}.")

    if width <= 0 or height <= 0:
        raise MediaError("That image has no dimensions.")

    if width * height > MAX_PIXELS:
        raise MediaError(
            f"That image is {width}x{height}, which is larger than EzVTT will "
            f"open ({MAX_PIXELS // 1_000_000} megapixels)."
        )

    return image_format, width, height


def store_upload(data: bytes, original_filename: str, directory: Path) -> StoredImage:
    """Validate an uploaded image and write it to ``directory``.

    Raises MediaError with a message suitable for display if anything is wrong.
    """
    if not data:
        raise MediaError("That file is empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(
            f"That file is {len(data) // (1024 * 1024)} MB, over the "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
        )

    image_format, width, height = _inspect(data)
    extension = ALLOWED_FORMATS[image_format]

    directory.mkdir(parents=True, exist_ok=True)
    filename = _storage_name(original_filename, extension)

    # Written whole via a temporary name and then moved, so a failure part-way
    # cannot leave a half-written file that later looks like a valid map.
    destination = directory / filename
    staging = directory / f".{filename}.part"
    try:
        staging.write_bytes(data)
        staging.replace(destination)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise MediaError(f"Could not save that file: {exc}") from exc

    return StoredImage(filename, width, height, image_format, len(data))


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #

def thumbnail_path(source: Path) -> Path:
    """Where a thumbnail for ``source`` lives.

    Always under data/thumbs/, never beside the original. Bundled Tom Cartos art
    is read-only under its licence -- writing a thumbnail next to it would be a
    licence violation as well as a bug. See ADR-005.
    """
    # Include a hash of the full path so two assets with the same basename from
    # different directories cannot collide.
    import hashlib

    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
    return config.THUMBS_DIR / f"{source.stem[:40]}-{digest}.webp"


def ensure_thumbnail(source: Path) -> Path | None:
    """Generate a thumbnail for ``source`` if it does not already exist.

    Returns the thumbnail path, or None if one could not be produced. A missing
    thumbnail is a cosmetic problem, never a reason to fail a request.
    """
    if not source.is_file():
        return None

    target = thumbnail_path(source)
    try:
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
    except OSError:
        pass

    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.draft("RGB", THUMB_MAX)  # cheap pre-scale for large JPEGs
            thumb = image.convert("RGBA")
            thumb.thumbnail(THUMB_MAX, Image.Resampling.LANCZOS)
            thumb.save(target, "WEBP", quality=82, method=4)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None

    return target


# --------------------------------------------------------------------------- #
# Safe resolution
# --------------------------------------------------------------------------- #

def resolve_within(root: Path, name: str) -> Path:
    """Resolve ``name`` inside ``root``, refusing anything that escapes it.

    Guards against traversal (``../``), absolute paths, and symlinks pointing
    outside the root. Checked after resolution, because that is the only point
    at which the true destination is known.
    """
    if not name or "\x00" in name:
        raise MediaError("Invalid filename.")

    root = root.resolve()
    candidate = (root / name).resolve()

    if candidate == root or root not in candidate.parents:
        raise MediaError("Invalid filename.")

    return candidate


def media_root(kind: str) -> Path:
    """Directory for a media kind. Unknown kinds are refused, never guessed."""
    roots = {
        "maps": config.MAPS_DIR,
        "uploads": config.UPLOADS_DIR,
        "bundled": config.BUNDLED_ASSETS_DIR,
        "thumbs": config.THUMBS_DIR,
    }
    if kind not in roots:
        raise MediaError(f"Unknown media kind: {kind}")
    return roots[kind]
