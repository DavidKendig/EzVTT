"""The parts of packaging that can be checked without building anything.

Two of these guard failures this project has already had. The licence closure
was a hand-written list that drifted the moment an upstream package changed its
own dependencies. And a data directory left out of the PyInstaller spec is
invisible until a GM opens a page in a downloaded build -- nothing imports
templates, so nothing notices they are gone.

Building and running an actual binary is `scripts/smoke_test.py`, which CI runs
on all three platforms.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from ezvtt import config

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "ezvtt.spec"


def load_generator():
    """Import scripts/gen_third_party_licenses.py, which is not a package."""
    path = REPO_ROOT / "scripts" / "gen_third_party_licenses.py"
    spec = importlib.util.spec_from_file_location("gen_third_party_licenses", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_generator()


# --------------------------------------------------------------------------- #
# Which licences a release has to carry
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("spec, expected", [
    ("fastapi>=0.115", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("python-multipart>=0.0.9      # multipart parsing", "python-multipart"),
    ("anyio<5,>=3.6.2", "anyio"),
    ("click (>=8.0)", "click"),
    ("colorama ; sys_platform == 'win32'", "colorama"),
    ("typing_extensions>=4.8.0; python_version < '3.13'", "typing_extensions"),
])
def test_a_requirement_name_is_read_off_any_spelling_of_a_requirement(spec, expected):
    assert generator._requirement_name(spec) == expected


@pytest.mark.parametrize("spec", [
    "",
    "   ",
    "# a comment",
    "-r other-requirements.txt",
    # An extra nobody asked for is not installed and ships nothing.
    "pytest>=6 ; extra == 'test'",
    "httpx; extra == \"all\"",
])
def test_things_that_are_not_shipped_requirements_are_skipped(spec):
    assert generator._requirement_name(spec) is None


def test_names_are_compared_the_way_packaging_compares_them():
    assert generator._canonical("typing_extensions") == generator._canonical(
        "Typing-Extensions"
    )
    assert generator._canonical("python.multipart") == "python-multipart"


def test_the_direct_requirements_are_read_from_requirements_txt():
    names = {name.lower() for name in generator.direct_requirements()}

    # The ones the README credits by name; if one of these disappears from the
    # file, that is a decision, not a typo.
    assert {"fastapi", "uvicorn", "websockets", "jinja2", "pillow"} <= names


def test_the_closure_reaches_dependencies_nobody_wrote_down():
    """starlette is not in requirements.txt. It ships all the same."""
    distributions, missing = generator.shipped_distributions()
    names = {name.lower() for name, _ in distributions}

    assert missing == []
    assert "starlette" in names          # via fastapi
    assert "markupsafe" in names         # via jinja2
    assert len(names) > len(generator.direct_requirements())


def test_every_shipped_distribution_reports_a_version():
    distributions, _ = generator.shipped_distributions()
    assert distributions
    assert all(dist.version for _, dist in distributions)


# --------------------------------------------------------------------------- #
# What the bundle has to contain
# --------------------------------------------------------------------------- #

def test_the_spec_lists_every_non_python_directory_in_the_package():
    """Nothing imports a template, so nothing notices when one is left out.

    A directory of files the program reads at runtime -- templates, static
    assets, SQL migrations -- has to be named in ezvtt.spec by hand. This fails
    the moment a new one is added without that line, rather than in a
    downloaded build.
    """
    spec_text = SPEC.read_text(encoding="utf-8")

    data_dirs = [
        directory for directory in (REPO_ROOT / "ezvtt").iterdir()
        if directory.is_dir()
        and directory.name != "__pycache__"
        and not any(directory.rglob("*.py"))
    ]

    assert data_dirs, "expected templates, static, and migrations to exist"
    for directory in data_dirs:
        assert f'"{directory.name}"' in spec_text, (
            f"ezvtt/{directory.name}/ is not in ezvtt.spec, so a packaged "
            f"build will not contain it"
        )


def test_the_spec_ships_the_licence_files():
    spec_text = SPEC.read_text(encoding="utf-8")
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"):
        assert name in spec_text


def test_the_entry_point_is_not_the_package_main():
    """PyInstaller runs its entry script without package context, which breaks
    every relative import in ezvtt/__main__.py -- in the binary only."""
    spec_text = SPEC.read_text(encoding="utf-8")
    assert "entrypoint.py" in spec_text
    assert (REPO_ROOT / "scripts" / "entrypoint.py").is_file()


# --------------------------------------------------------------------------- #
# Finding the documents a build carries
# --------------------------------------------------------------------------- #

def test_the_licence_documents_are_locatable():
    for name in ("LICENSE", "NOTICE"):
        assert config.bundled_file(name) is not None


def test_a_document_that_is_not_there_is_none_rather_than_a_crash():
    assert config.bundled_file("NO-SUCH-FILE.txt") is None


def test_the_data_root_can_be_moved(tmp_path, monkeypatch):
    """EZVTT_DATA_ROOT is how a campaign lives somewhere other than beside the
    executable -- and how the smoke test avoids touching anything real."""
    monkeypatch.setenv("EZVTT_DATA_ROOT", str(tmp_path))
    assert config._project_root() == tmp_path.resolve()


def test_a_frozen_build_keeps_its_state_next_to_the_executable(tmp_path, monkeypatch):
    """A one-file bundle unpacks into a temporary directory that is deleted on
    exit. A campaign database written there would be lost, silently."""
    monkeypatch.delenv("EZVTT_DATA_ROOT", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "ezvtt.exe"))

    assert config._project_root() == tmp_path.resolve()
