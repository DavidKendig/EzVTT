"""
Wiki content sourced from a GitHub repository (an Obsidian Markdown vault).

The repo is downloaded as a tarball (no `git` dependency) and cached locally,
then individual pages are rendered to HTML on request. Obsidian `[[wikilinks]]`
and `![[image embeds]]` are translated into working links.

NOTE: fetching is done here in the Django UI layer because the wiki *is* a
client-side component. In a hardened deploy the outbound fetch would be mediated
by the Java gateway (which owns networking). All file access is path-traversal
guarded against the cache root.
"""
import io
import re
import shutil
import tarfile
import urllib.request
from html import escape
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib

REPO = "DavidKendig/AetherniaFantasyRPGCampaign"
BRANCH = "main"
TARBALL_URL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{BRANCH}"

BASE_DIR = Path(__file__).resolve().parent.parent  # the client/ folder
CACHE_DIR = BASE_DIR / ".wiki_cache"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MD_EXTENSIONS = ["extra", "toc", "sane_lists", "admonition"]


# --- cache location ---------------------------------------------------------

def vault_root():
    """The single top-level dir the tarball extracts to (repo-branch/), or None."""
    if not CACHE_DIR.exists():
        return None
    subdirs = [p for p in CACHE_DIR.iterdir() if p.is_dir()]
    return subdirs[0] if subdirs else None


def is_synced():
    root = vault_root()
    return root is not None and next(root.rglob("*.md"), None) is not None


def sync():
    """Download the repo tarball and extract it into the cache (replacing any old copy)."""
    req = urllib.request.Request(TARBALL_URL, headers={"User-Agent": "EzVTT-wiki"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(CACHE_DIR, filter="data")  # 'data' filter blocks unsafe paths


# --- safe path resolution ---------------------------------------------------

def safe_resolve(relpath):
    """Resolve a relative path inside the vault, or None if it escapes the root."""
    root = vault_root()
    if not root:
        return None
    target = (root / relpath).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None  # path traversal attempt
    return target


# --- indexes (for wikilink resolution and the nav tree) ---------------------

def _is_hidden(rel):
    return any(part.startswith(".") for part in rel.parts)


def list_pages():
    """Sorted POSIX relpaths of every .md page (config/hidden dirs excluded)."""
    root = vault_root()
    if not root:
        return []
    out = []
    for p in root.rglob("*.md"):
        rel = p.relative_to(root)
        if not _is_hidden(rel):
            out.append(rel.as_posix())
    return sorted(out)


def _indexes():
    """basename -> relpath maps for Markdown pages and for image files."""
    root = vault_root()
    md_idx, media_idx = {}, {}
    if not root:
        return md_idx, media_idx
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if _is_hidden(rel):
            continue
        ext = p.suffix.lower()
        if ext == ".md":
            md_idx.setdefault(p.stem, rel.as_posix())
        elif ext in IMAGE_EXTS:
            media_idx.setdefault(p.name, rel.as_posix())
            media_idx.setdefault(p.stem, rel.as_posix())
    return md_idx, media_idx


# --- rendering --------------------------------------------------------------

_WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")


def _convert_wikilinks(text, md_idx, media_idx):
    def repl(m):
        target, _, alias = m.group(2).partition("|")
        target = target.split("#")[0].strip()
        alias = alias.strip() or target
        ext = Path(target).suffix.lower()
        if ext in IMAGE_EXTS:  # image embed: ![[picture.png]]
            rel = media_idx.get(target) or media_idx.get(Path(target).stem)
            return f"![{alias}](/wiki-media/{quote(rel)})" if rel else f"*[missing image: {escape(target)}]*"
        rel = md_idx.get(target)  # page link: [[Some Page]]
        if rel:
            return f"[{alias}](/wiki/{quote(rel[:-3])})"
        return alias  # unresolved link -> plain text
    return _WIKILINK_RE.sub(repl, text)


def read_source(page):
    """Markdown source for a page (relpath without .md), or None."""
    target = safe_resolve(page + ".md")
    if target and target.is_file():
        return target.read_text(encoding="utf-8", errors="replace")
    return None


def render_page(page):
    """Rendered HTML for a page, or None if it doesn't exist."""
    src = read_source(page)
    if src is None:
        return None
    md_idx, media_idx = _indexes()
    src = _convert_wikilinks(src, md_idx, media_idx)
    return md_lib.markdown(src, extensions=MD_EXTENSIONS, output_format="html5")


# --- navigation tree --------------------------------------------------------

def nav_html(current=None):
    """Collapsible folder tree of all pages; ancestors of `current` open."""
    tree = {}
    for rel in list_pages():
        parts = rel.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append((parts[-1], rel))
    return _render_tree(tree, current or "")


def _render_tree(node, current, prefix=""):
    html = ["<ul class='wiki-tree list-unstyled mb-0'>"]
    for folder in sorted(k for k in node if k != "__files__"):
        folder_path = f"{prefix}{folder}/"
        is_open = current.startswith(folder_path)
        html.append(f"<li><details{' open' if is_open else ''}>"
                    f"<summary>{escape(folder)}</summary>")
        html.append(_render_tree(node[folder], current, folder_path))
        html.append("</details></li>")
    for fname, rel in sorted(node.get("__files__", [])):
        active = " active" if rel == current else ""
        url = "/wiki/" + quote(rel[:-3])
        html.append(f"<li><a class='wiki-link{active}' href='{url}'>{escape(fname[:-3])}</a></li>")
    html.append("</ul>")
    return "".join(html)
