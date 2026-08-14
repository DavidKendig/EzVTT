#!/usr/bin/env python3
"""Generate THIRD_PARTY_LICENSES.md from the installed dependencies.

License texts are read byte-exact from each distribution's own ``.dist-info``
metadata rather than being transcribed by hand. Hand-copied license text drifts
from the real thing and silently misattributes clauses, which makes an
authoritative-looking file that is wrong -- worse than having no file at all.

Run after ``pip install -r requirements.txt``, and again whenever dependencies
change::

    python scripts/gen_third_party_licenses.py

Exits non-zero if any dependency's license text cannot be located, so that a
compliance gap fails the build instead of disappearing quietly.
"""

from __future__ import annotations

import argparse
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "THIRD_PARTY_LICENSES.md"

# Distributions EzVTT depends on, directly or transitively, that ship in a
# release. Keep in sync with requirements.txt; the script verifies the set is
# actually installed and complains if it is not.
DISTRIBUTIONS = [
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "annotated-types",
    "typing_extensions",
    "uvicorn",
    "websockets",
    "python-multipart",
    "Jinja2",
    "MarkupSafe",
    "pillow",
    "Markdown",
    "qrcode",
    "h11",
    "anyio",
    "click",
    "idna",
    "sniffio",
]

# Optional extras: absent from a minimal install, included when present.
OPTIONAL_DISTRIBUTIONS = ["zeroconf", "ifaddr", "colorama"]

# Filenames inside a dist-info that hold license text, in preference order.
LICENSE_FILE_HINTS = (
    "LICENSE",
    "LICENCE",
    "COPYING",
    "NOTICE",
    "AUTHORS",
)

HEADER = """# Third-Party Licenses

EzVTT's own source code is licensed under the Apache License, Version 2.0 --
see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

This file reproduces the license texts of the third-party Python packages EzVTT
distributes or depends on, as required by those licenses. **It is generated
automatically** by `scripts/gen_third_party_licenses.py`, which reads each
license verbatim from the installed package's own metadata. Do not edit it by
hand -- regenerate it instead:

```
python scripts/gen_third_party_licenses.py
```

> **Artwork is not covered here.** Map assets bundled with or downloaded by
> EzVTT are the work of Tom Cartos, licensed under
> [Tom's Open Map License](https://www.tomcartos.com/toms-open-map-license),
> and are neither Apache-licensed nor relicensed by this project. See
> [`NOTICE`](NOTICE) and the Acknowledgements section of [`README.md`](README.md).

---
"""


class LicenseGap(Exception):
    """A distribution is installed but ships no locatable license text."""


def _dist_info_dir(dist: metadata.Distribution) -> Path | None:
    """Best-effort filesystem location of the distribution's metadata dir."""
    located = getattr(dist, "_path", None)
    if isinstance(located, Path) and located.is_dir():
        return located
    return None


def _declared_license(dist: metadata.Distribution) -> str:
    """Short license identifier from package metadata, for the summary table."""
    meta = dist.metadata

    # PEP 639 license expression, preferred where present.
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()

    # Trove classifiers are more reliable than the free-text License field,
    # which packages fill in with everything from "MIT" to a full license body.
    classifiers = meta.get_all("Classifier") or []
    licenses = [
        c.split("::")[-1].strip()
        for c in classifiers
        if c.startswith("License ::")
    ]
    if licenses:
        return ", ".join(dict.fromkeys(licenses))

    free_text = (meta.get("License") or "").strip()
    if free_text and "\n" not in free_text and len(free_text) < 64:
        return free_text

    return "See license text below"


def _license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """Return ``(filename, text)`` for every license file the package ships."""
    found: list[tuple[str, str]] = []

    # PEP 639 License-File entries are authoritative when the package sets them.
    declared = dist.metadata.get_all("License-File") or []
    info_dir = _dist_info_dir(dist)

    candidates: list[Path] = []
    if info_dir is not None:
        for name in declared:
            for candidate in (
                info_dir / name,
                info_dir / "licenses" / name,
                info_dir / "license_files" / name,
            ):
                if candidate.is_file():
                    candidates.append(candidate)
                    break

        # Fall back to scanning the metadata dir for license-shaped filenames.
        if not candidates:
            search_dirs = [
                info_dir,
                info_dir / "licenses",
                info_dir / "license_files",
            ]
            for directory in search_dirs:
                if not directory.is_dir():
                    continue
                for path in sorted(directory.iterdir()):
                    if not path.is_file():
                        continue
                    stem = path.name.upper()
                    if any(stem.startswith(hint) for hint in LICENSE_FILE_HINTS):
                        candidates.append(path)

    seen: set[str] = set()
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        found.append((path.name, text))

    return found


def _render(name: str, dist: metadata.Distribution) -> tuple[str, str, str]:
    """Render one package section. Returns ``(section, version, license_id)``."""
    version = dist.version
    license_id = _declared_license(dist)
    texts = _license_texts(dist)

    if not texts:
        raise LicenseGap(
            f"{name} {version} ships no license file in its metadata. "
            f"Obtain the license text from the project's repository and add it "
            f"to scripts/manual_licenses/{name}.txt, or remove the dependency."
        )

    homepage = (
        dist.metadata.get("Home-page")
        or dist.metadata.get("Project-URL")
        or ""
    ).strip()

    lines = [f"## {name} {version}", ""]
    lines.append(f"**License:** {license_id}")
    if homepage:
        lines.append("  ")
        lines.append(f"**Homepage:** {homepage}")
    lines.append("")

    for filename, text in texts:
        lines.append("<details>")
        lines.append(f"<summary><code>{filename}</code></summary>")
        lines.append("")
        lines.append("```")
        # Guard against a license file containing a fence and breaking layout.
        lines.append(text.replace("```", "'''"))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines), version, license_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed file is current; do not rewrite it.",
    )
    args = parser.parse_args(argv)

    sections: list[str] = []
    summary: list[tuple[str, str, str]] = []
    gaps: list[str] = []
    missing: list[str] = []

    for name in DISTRIBUTIONS + OPTIONAL_DISTRIBUTIONS:
        optional = name in OPTIONAL_DISTRIBUTIONS
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if not optional:
                missing.append(name)
            continue

        try:
            section, version, license_id = _render(name, dist)
        except LicenseGap as exc:
            gaps.append(str(exc))
            continue

        sections.append(section)
        summary.append((name, version, license_id))

    if missing:
        print(
            "Not installed (run `pip install -r requirements.txt` first):\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        return 2

    if gaps:
        print("License text missing:\n  " + "\n  ".join(gaps), file=sys.stderr)
        return 3

    table = ["| Package | Version | License |", "|---|---|---|"]
    for name, version, license_id in sorted(summary, key=lambda r: r[0].lower()):
        table.append(f"| {name} | {version} | {license_id} |")

    body = "\n".join(
        [HEADER, "", "\n".join(table), "", "---", "", "\n---\n\n".join(sections)]
    ).rstrip() + "\n"

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != body:
            print(
                f"{OUTPUT.name} is out of date. Run: "
                f"python scripts/gen_third_party_licenses.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.name} is current ({len(summary)} packages).")
        return 0

    OUTPUT.write_text(body, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)} covering {len(summary)} packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
