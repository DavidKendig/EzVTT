"""Read-only access to an Obsidian vault.

A campaign vault is a dangerous thing to expose. It holds the players' handouts
and the GM's plot outline in the same folder tree, often with names like
"Session 12 - the traitor is Marcus.md". So the model here is **deny by
default**: nothing is visible to players until the GM ticks a folder, and every
request is checked against that list rather than merely rendered from a path the
client supplied.

Three separate guards, because any one of them failing alone should not leak:

1. ``media.resolve_within`` confines every path to the vault root, defeating
   ``../`` and absolute paths.
2. The resolved path is re-checked against the allow-list, so a symlink that
   escapes the root or points at an unlisted folder is refused even though it
   resolved cleanly.
3. Rendered HTML is sanitised, so a note containing ``<script>`` cannot run in
   a player's browser.

Nothing here writes. The vault is the GM's own notes, edited in Obsidian.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import db, media

log = logging.getLogger("ezvtt.vault")

VAULT_PATH_SETTING = "vault_path"
ALLOWED_DIRS_SETTING = "vault_allowed_dirs"

READABLE_SUFFIXES = {".md", ".markdown", ".txt"}
EMBEDDABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Folders Obsidian and friends use for their own state. Showing them is noise at
# best and leaks plugin configuration at worst.
SKIP_DIRS = {".obsidian", ".trash", ".git", ".github", "node_modules", "__pycache__"}

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_RESULTS = 50
MAX_TREE_ENTRIES = 5000


class VaultError(ValueError):
    """The message is safe to show a user."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def vault_root() -> Path | None:
    """The configured vault directory, or None if unset or missing."""
    configured = db.get_setting(VAULT_PATH_SETTING, "").strip()
    if not configured:
        return None

    path = Path(configured).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def set_vault_root(path: str) -> Path | None:
    """Point EzVTT at a vault. Clearing the path disables the wiki entirely."""
    path = (path or "").strip()
    if not path:
        db.set_setting(VAULT_PATH_SETTING, "")
        db.set_setting(ALLOWED_DIRS_SETTING, "")
        return None

    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise VaultError(f"That path could not be read: {exc}") from exc

    if not resolved.is_dir():
        raise VaultError("That is not a folder EzVTT can open.")

    db.set_setting(VAULT_PATH_SETTING, str(resolved))
    # Changing the vault clears the allow-list. Folder names in a new vault mean
    # different things, and silently carrying "Handouts" across would be a way
    # to share the wrong Handouts.
    db.set_setting(ALLOWED_DIRS_SETTING, "")
    return resolved


def allowed_dirs() -> list[str]:
    """Vault-relative folders players may read. Empty means nothing is shared."""
    raw = db.get_setting(ALLOWED_DIRS_SETTING, "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split("\n") if part.strip()]


def set_allowed_dirs(folders: list[str]) -> list[str]:
    """Replace the allow-list. Entries must be real folders inside the vault."""
    root = vault_root()
    if root is None:
        raise VaultError("Set a vault folder first.")

    cleaned: list[str] = []
    for folder in folders:
        relative = (folder or "").strip().replace("\\", "/").strip("/")
        if not relative:
            # An empty entry would mean the vault root, i.e. share everything.
            # That has to be spelled out, not arrived at by a stray blank line.
            continue
        try:
            target = media.resolve_within(root, relative)
        except media.MediaError as exc:
            raise VaultError(f"{folder!r} is not inside the vault.") from exc
        if not target.is_dir():
            raise VaultError(f"{folder!r} is not a folder.")
        if relative not in cleaned:
            cleaned.append(relative)

    db.set_setting(ALLOWED_DIRS_SETTING, "\n".join(cleaned))
    return cleaned


def is_configured() -> bool:
    return vault_root() is not None


# --------------------------------------------------------------------------- #
# Permission
# --------------------------------------------------------------------------- #

def _relative_to_root(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def may_read(relative: str, is_gm: bool) -> bool:
    """Whether this vault-relative path is readable by this audience.

    GMs see the whole vault. Players see only what is inside an allow-listed
    folder -- and an empty allow-list means nothing at all, which is the default
    and the safe answer.
    """
    if is_gm:
        return True

    relative = relative.replace("\\", "/").strip("/")
    for allowed in allowed_dirs():
        if relative == allowed or relative.startswith(allowed + "/"):
            return True
    return False


def resolve(relative: str, is_gm: bool) -> Path:
    """Resolve a vault-relative path, or raise.

    Checks containment *and* permission against the resolved path, so a symlink
    pointing outside the vault or into an unlisted folder is refused even though
    it resolved without error.
    """
    root = vault_root()
    if root is None:
        raise VaultError("No vault folder has been set.")

    try:
        target = media.resolve_within(root, relative)
    except media.MediaError as exc:
        raise VaultError("That is not a file in the vault.") from exc

    # Re-derive the relative path from what the filesystem actually resolved to,
    # rather than trusting the string the client sent.
    try:
        actual = _relative_to_root(root, target)
    except ValueError:
        raise VaultError("That is not a file in the vault.") from None

    if not may_read(actual, is_gm):
        # Deliberately the same message as "not found": telling a player that a
        # folder exists but is off-limits is itself a spoiler.
        raise VaultError("That is not a file in the vault.")

    return target


# --------------------------------------------------------------------------- #
# Browsing
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    children: list[Entry] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name, "path": self.path, "is_dir": self.is_dir,
        }
        if self.children is not None:
            data["children"] = [child.to_dict() for child in self.children]
        return data


def tree(is_gm: bool) -> list[dict[str, Any]]:
    """The folder tree this audience may browse."""
    root = vault_root()
    if root is None:
        return []

    if is_gm:
        roots = [root]
    else:
        roots = []
        for allowed in allowed_dirs():
            try:
                roots.append(media.resolve_within(root, allowed))
            except media.MediaError:
                continue

    budget = [MAX_TREE_ENTRIES]
    entries: list[Entry] = []
    for start in roots:
        if not start.is_dir():
            continue
        built = _build(start, root, budget)
        if is_gm and start == root:
            entries.extend(built)
        else:
            entries.append(Entry(
                name=start.name, path=_relative_to_root(root, start),
                is_dir=True, children=built,
            ))

    return [entry.to_dict() for entry in entries]


def _build(directory: Path, root: Path, budget: list[int]) -> list[Entry]:
    """Recursive listing, bounded so a pathological vault cannot hang the server."""
    if budget[0] <= 0:
        return []

    entries: list[Entry] = []
    try:
        children = sorted(
            directory.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except OSError:
        return []

    for child in children:
        if budget[0] <= 0:
            break
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue

        # Do not follow links out of the vault: an Obsidian vault with a symlink
        # to the home directory would otherwise become a file browser.
        try:
            if child.is_symlink() and root not in child.resolve().parents:
                continue
        except OSError:
            continue

        budget[0] -= 1
        if child.is_dir():
            entries.append(Entry(
                name=child.name, path=_relative_to_root(root, child),
                is_dir=True, children=_build(child, root, budget),
            ))
        elif child.suffix.lower() in READABLE_SUFFIXES:
            entries.append(Entry(
                name=child.stem, path=_relative_to_root(root, child), is_dir=False,
            ))

    return entries


def search(query: str, is_gm: bool, limit: int = MAX_SEARCH_RESULTS) -> list[dict]:
    """Find notes by name and content, within what this audience may read."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    root = vault_root()
    if root is None:
        return []

    needle = query.lower()
    results: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*")):
        if len(results) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in READABLE_SUFFIXES:
            continue
        if any(part.startswith(".") or part in SKIP_DIRS for part in path.parts):
            continue

        try:
            relative = _relative_to_root(root, path)
        except ValueError:
            continue
        # The permission check runs before the file is opened, so a player's
        # search never even reads a note they may not see.
        if not may_read(relative, is_gm):
            continue

        excerpt = ""
        if needle in path.stem.lower():
            excerpt = ""
        else:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            position = text.lower().find(needle)
            if position < 0:
                continue
            start = max(0, position - 60)
            excerpt = text[start:position + 120].replace("\n", " ").strip()

        results.append({"name": path.stem, "path": relative, "excerpt": excerpt})

    return results


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def read(relative: str, is_gm: bool) -> dict[str, Any]:
    """Render one note to sanitised HTML."""
    path = resolve(relative, is_gm)

    if not path.is_file():
        raise VaultError("That is not a file in the vault.")
    if path.suffix.lower() not in READABLE_SUFFIXES:
        raise VaultError("EzVTT can only show text and Markdown notes.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise VaultError("That note is too large to display.")

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise VaultError("That note could not be read.") from exc

    frontmatter, body = _split_frontmatter(text)
    body = _rewrite_links(body, path, is_gm)

    try:
        import markdown

        rendered = markdown.markdown(
            body, extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        )
    except ImportError:
        # Better a readable plain-text note than a broken page.
        rendered = f"<pre>{html.escape(body)}</pre>"

    return {
        "name": path.stem,
        "path": _relative_to_root(vault_root(), path),
        "frontmatter": frontmatter,
        "html": sanitise(rendered),
    }


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Peel off a YAML frontmatter block.

    Parsed shallowly on purpose -- key/value pairs are all Obsidian frontmatter
    is used for here, and pulling in a YAML parser to read a tags line would be
    a dependency for nothing.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            fields[key.strip()] = value.strip()
    return fields, text[match.end():]


def _rewrite_links(body: str, source: Path, is_gm: bool) -> str:
    """Turn Obsidian ``[[wikilinks]]`` and ``![[embeds]]`` into real links.

    A link to a note the reader may not see is rendered as plain text rather
    than a dead link, so a player cannot map the GM's folder structure by
    collecting broken references.
    """
    root = vault_root()
    if root is None:
        return body

    def resolve_target(name: str, suffixes: set[str]) -> str | None:
        wanted = name.strip().replace("\\", "/").strip("/")
        if not wanted:
            return None

        candidates = [wanted]
        if not Path(wanted).suffix:
            candidates += [f"{wanted}{suffix}" for suffix in sorted(suffixes)]

        for candidate in candidates:
            try:
                target = media.resolve_within(root, candidate)
            except media.MediaError:
                continue
            if target.is_file():
                relative = _relative_to_root(root, target)
                return relative if may_read(relative, is_gm) else None

        # Obsidian links by note name regardless of folder, so fall back to a
        # search by stem across the vault.
        stem = Path(wanted).stem.lower()
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if path.stem.lower() != stem:
                continue
            try:
                relative = _relative_to_root(root, path)
            except ValueError:
                continue
            if may_read(relative, is_gm):
                return relative
        return None

    def embed(match: re.Match) -> str:
        target = resolve_target(match.group(1), EMBEDDABLE_SUFFIXES)
        label = html.escape(match.group(2) or match.group(1))
        if target is None:
            return label
        from urllib.parse import quote

        return f'<img src="/api/vault/file?path={quote(target)}" alt="{label}">'

    def link(match: re.Match) -> str:
        label = html.escape(match.group(2) or match.group(1))
        target = resolve_target(match.group(1), READABLE_SUFFIXES)
        if target is None:
            return label
        from urllib.parse import quote

        return f'<a href="#" data-vault-link="{quote(target)}">{label}</a>'

    return _WIKILINK_RE.sub(link, _EMBED_RE.sub(embed, body))


# --------------------------------------------------------------------------- #
# Sanitising
# --------------------------------------------------------------------------- #

# An allow-list, not a block-list: anything unrecognised is dropped rather than
# passed through, so a tag nobody thought of cannot slip past.
ALLOWED_TAGS = {
    "p", "br", "hr", "em", "strong", "b", "i", "u", "s", "del", "ins", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code", "kbd", "samp", "var",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "a", "img", "span", "div", "sup", "sub", "abbr", "small",
}

ALLOWED_ATTRS = {
    "a": {"href", "title", "data-vault-link"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan", "align"},
    "td": {"colspan", "rowspan", "align"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
    "abbr": {"title"},
}

VOID_TAGS = {"br", "hr", "img"}

# Schemes a link may use. Anything else -- javascript:, data:, vbscript:, file:
# -- is dropped.
SAFE_SCHEMES = {"http", "https", "mailto"}

# A scheme is everything before the first colon, provided no /, ?, or # comes
# first. "Notes/Chapter 2: Endings.md" is a relative path, not a "Notes/Chapter
# 2" scheme, so the delimiters have to be checked too.
_SCHEME_RE = re.compile(r"\A([A-Za-z][A-Za-z0-9+.-]*):")


def safe_url(value: str) -> bool:
    """Whether a href or src may be kept.

    Written as "reject unknown schemes" rather than "starts with a safe
    character". The first version of this ended in a character class that
    matched any word character, which happily let ``javascript:alert(1)``
    through -- the precise thing it was meant to stop.
    """
    if value is None:
        return False

    # Control characters and whitespace are stripped by browsers before the
    # scheme is parsed, so "java\tscript:x" runs. Strip them here as well or the
    # comparison below is against a different string than the browser sees.
    cleaned = "".join(
        ch for ch in value if ch not in "\t\r\n" and ord(ch) > 0x1F
    ).strip()
    if not cleaned:
        return False

    delimiter = min(
        (cleaned.find(c) for c in "/?#" if cleaned.find(c) != -1), default=len(cleaned)
    )
    match = _SCHEME_RE.match(cleaned[:delimiter + 1])
    if match:
        return match.group(1).lower() in SAFE_SCHEMES

    # No scheme: a relative path, an anchor, or a root-relative URL.
    return True


class _Sanitiser(HTMLParser):
    """Rebuild HTML keeping only allow-listed tags and attributes.

    The vault is the GM's own folder, so this is not defence against a hostile
    author so much as against pasted content -- a note copied from a web page
    can easily carry a tracking pixel or a script tag, and it would run in every
    player's browser.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag, attrs):
        # Drop the *content* of a script or style, not just its tags.
        if tag in ("script", "style", "iframe", "object", "embed", "form"):
            self._suppress += 1
            return
        if self._suppress or tag not in ALLOWED_TAGS:
            return

        kept = []
        for name, value in attrs:
            if name not in ALLOWED_ATTRS.get(tag, set()):
                continue
            if value is None:
                continue
            if name in ("href", "src") and not safe_url(value):
                continue
            kept.append(f' {name}="{html.escape(value, quote=True)}"')

        closer = "/" if tag in VOID_TAGS else ""
        self.parts.append(f"<{tag}{''.join(kept)}{closer}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "embed", "form"):
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppress:
            self.parts.append(html.escape(data))


def sanitise(markup: str) -> str:
    """Strip anything from rendered vault HTML that could act on a page."""
    parser = _Sanitiser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        log.warning("Could not sanitise vault HTML; showing it as text.")
        return f"<pre>{html.escape(markup)}</pre>"
    return "".join(parser.parts)
