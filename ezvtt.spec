# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file EzVTT build.

Built by scripts/build.ps1 and scripts/build.sh, which regenerate the
third-party licence file first -- see ADR-008 and ADR-016.

Three things this spec exists to get right:

**Data files.** Templates, static assets, and SQL migrations are not Python and
PyInstaller will not find them by following imports. They are added under
``ezvtt/`` so that ``config.PACKAGE_ROOT`` resolves them inside the extraction
directory exactly as it does in a source checkout.

**Dynamic imports.** uvicorn selects its event loop, HTTP parser, and WebSocket
implementation by importing them by name at runtime, and Markdown loads its
extensions the same way. Neither is visible to static analysis, so both are
collected wholesale.

**Licences.** A bundled build redistributes its dependencies, so their licence
texts ship inside it and ``ezvtt --licences`` prints them. The build refuses to
start if that file has not been generated.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()          # noqa: F821 -- injected by PyInstaller

LICENCE_FILE = ROOT / "THIRD_PARTY_LICENSES.md"
if not LICENCE_FILE.is_file():
    raise SystemExit(
        "THIRD_PARTY_LICENSES.md is missing. A packaged build redistributes its\n"
        "dependencies and must carry their licence texts (ADR-008). Run:\n"
        "    python scripts/gen_third_party_licenses.py"
    )

datas = [
    (str(ROOT / "ezvtt" / "templates"), "ezvtt/templates"),
    (str(ROOT / "ezvtt" / "static"), "ezvtt/static"),
    (str(ROOT / "ezvtt" / "migrations"), "ezvtt/migrations"),
    # At the root of the bundle, where config.bundled_file looks for them.
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE"), "."),
    (str(LICENCE_FILE), "."),
]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("markdown")
    + ["websockets", "websockets.legacy", "anyio._backends._asyncio"]
)

# Nothing here draws a window, plots a graph, or runs a notebook. Excluding
# these keeps a build that has them installed for other reasons from quietly
# growing by tens of megabytes.
excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "IPython",
    "PIL.ImageTk",
    "PIL.ImageQt",
    "PyQt5",
    "PySide2",
]

analysis = Analysis(                     # noqa: F821
    # scripts/entrypoint.py, not ezvtt/__main__.py: PyInstaller runs its entry
    # script as __main__ with no package context, which breaks every relative
    # import in that module -- in the built binary only, never in development.
    [str(ROOT / "scripts" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)                 # noqa: F821

exe = EXE(                               # noqa: F821
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ezvtt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX-packed binaries are a reliable way to be
                              # quarantined by antivirus, which is a far worse
                              # first impression than a larger download.
    runtime_tmpdir=None,
    # A console, deliberately. EzVTT prints the address players join at, and on
    # Windows a hidden console process is exactly the shape antivirus reacts to.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
