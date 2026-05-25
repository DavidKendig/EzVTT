"""
Battlemap & token image storage for the GM admin area.

Each image category ("battlemaps", "tokens") has two sources:
  * SAMPLES  — read-only starter images shipped in the repo ("sample images/").
               These are the hard-coded starting set and are never modified.
  * UPLOADS  — images the GM uploads at runtime (client/media/<kind>/, which is
               gitignored).

Mirrors the safety stance of wiki_content.py: every file access is basename-only
and path-traversal guarded against its source directory.
"""
import json
from pathlib import Path
from urllib.parse import quote

from django.utils.text import get_valid_filename

BASE_DIR = Path(__file__).resolve().parent.parent          # the client/ folder
PROJECT_DIR = BASE_DIR.parent                              # repo root
SAMPLE_DIR = PROJECT_DIR / "sample images"                 # tracked starter images
UPLOAD_DIR = BASE_DIR / "media"                            # runtime uploads (gitignored)
METADATA_FILE = UPLOAD_DIR / "metadata.json"               # per-image grid sizes

# Internal kind -> capitalized sample subfolder name.
KINDS = {"battlemaps": "Battlemaps", "tokens": "Tokens"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Default grid a battlemap uses until the GM fits one to the image.
DEFAULT_GRID = {"cols": 10, "rows": 10}
GRID_MIN, GRID_MAX = 1, 100


def _source_dir(kind, source):
    """Directory holding images for (kind, source), or None if either is unknown."""
    if kind not in KINDS:
        return None
    if source == "sample":
        return SAMPLE_DIR / KINDS[kind]
    if source == "upload":
        return UPLOAD_DIR / kind
    return None


def list_images(kind):
    """Images for a category: hard-coded samples first, then uploads.

    Each entry is {"name", "source", "url", "grid"}. ``grid`` is the saved
    {cols, rows} the GM fitted to the image (DEFAULT_GRID until set).
    """
    meta = _load_meta()
    out = []
    for source in ("sample", "upload"):
        d = _source_dir(kind, source)
        if not d or not d.is_dir():
            continue
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                out.append({
                    "name": p.name,
                    "source": source,
                    "url": f"/media/{kind}/{source}/{quote(p.name)}",
                    "grid": _grid_from(meta, kind, source, p.name),
                })
    return out


def resolve(kind, source, name):
    """Absolute path for a stored image, or None if invalid / escapes its dir."""
    base = _source_dir(kind, source)
    if base is None:
        return None
    if name != Path(name).name:          # reject any path components
        return None
    target = (base / name).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None                      # path-traversal attempt
    if target.is_file() and target.suffix.lower() in IMAGE_EXTS:
        return target
    return None


def save_upload(kind, uploaded):
    """Persist an uploaded image under client/media/<kind>/.

    Returns the stored filename. Raises ValueError on a bad kind / file type.
    Never overwrites an existing file (appends -1, -2, ... on collision).
    """
    if kind not in KINDS:
        raise ValueError("unknown image category")
    ext = Path(uploaded.name).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise ValueError("unsupported file type (use PNG, JPG, GIF, WEBP, BMP, or SVG)")
    name = get_valid_filename(uploaded.name)
    if not name:
        raise ValueError("invalid file name")

    dest_dir = _source_dir(kind, "upload")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while dest.exists():
        dest = dest_dir / f"{stem}-{i}{suffix}"
        i += 1

    with open(dest, "wb") as f:
        for chunk in uploaded.chunks():
            f.write(chunk)
    return dest.name


# --- grid metadata ----------------------------------------------------------
#
# Grid size (cols x rows) the GM fits to a battlemap, stored per-image in a JSON
# registry under client/media/. Kept here (not on the image file) so the
# read-only sample images can carry metadata too.

def _meta_key(kind, source, name):
    return f"{kind}/{source}/{name}"


def _load_meta():
    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_meta(data):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clamp(v, fallback):
    try:
        return max(GRID_MIN, min(GRID_MAX, int(v)))
    except (TypeError, ValueError):
        return fallback


def _grid_from(meta, kind, source, name):
    entry = meta.get(_meta_key(kind, source, name), {})
    return {
        "cols": _clamp(entry.get("cols"), DEFAULT_GRID["cols"]),
        "rows": _clamp(entry.get("rows"), DEFAULT_GRID["rows"]),
    }


def get_grid(kind, source, name):
    """Saved {cols, rows} for an image, or DEFAULT_GRID if none is stored."""
    return _grid_from(_load_meta(), kind, source, name)


def set_grid(kind, source, name, cols, rows):
    """Persist the grid the GM fitted to an image. Raises ValueError if unknown."""
    if resolve(kind, source, name) is None:
        raise ValueError("unknown image")
    grid = {"cols": _clamp(cols, DEFAULT_GRID["cols"]),
            "rows": _clamp(rows, DEFAULT_GRID["rows"])}
    data = _load_meta()
    data[_meta_key(kind, source, name)] = grid
    _save_meta(data)
    return grid
