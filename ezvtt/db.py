"""SQLite access and the migration runner.

Deliberately thin: stdlib ``sqlite3``, plain ``.sql`` migration files, no ORM.
See ADR-002 for why.

Every query in this project must be parameterised. Never build SQL by string
formatting -- ``?`` placeholders exist, and the one place a table name has to be
interpolated should be a hardcoded constant, not user input.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config

_MIGRATION_PATTERN = re.compile(r"^(\d{3})_[\w-]+\.sql$")

# One connection per thread. sqlite3 connections are not safe to share across
# threads, and the alternative -- a connection per request -- discards the
# statement cache and re-runs PRAGMAs on a hot path.
_local = threading.local()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite. Without this the ON DELETE
    # CASCADE clauses throughout the schema are silently decorative.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the display window and player views read while the GM writes,
    # instead of blocking on a single writer lock mid-session.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Return this thread's connection, opening it on first use."""
    db_path = path or config.DB_PATH
    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == db_path:
        return existing

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _configure(conn)
    _local.conn = conn
    _local.path = db_path
    return conn


def close() -> None:
    """Close this thread's connection, if it has one."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Run a block in a transaction, committing on success.

    ``sqlite3``'s implicit transaction handling does not cover DDL, so this
    issues BEGIN explicitly rather than relying on it.
    """
    conn = connect(path)
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #

def _discover(directory: Path) -> list[tuple[int, Path]]:
    """Migration files, ordered. Rejects malformed or duplicated version numbers."""
    found: dict[int, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_PATTERN.match(path.name)
        if not match:
            raise ValueError(
                f"Migration {path.name!r} does not match NNN_name.sql -- "
                f"rename it so ordering is unambiguous."
            )
        version = int(match.group(1))
        if version in found:
            raise ValueError(
                f"Duplicate migration version {version:03d}: "
                f"{found[version].name} and {path.name}."
            )
        found[version] = path
    return sorted(found.items())


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(path: Path | None = None, directory: Path | None = None) -> list[str]:
    """Apply any pending migrations in order. Returns the names applied.

    Each migration runs in its own transaction, so a failure part-way leaves the
    database at the last complete version rather than half-migrated.
    """
    directory = directory or config.MIGRATIONS_DIR
    conn = connect(path)
    already = applied_versions(conn)

    applied: list[str] = []
    for version, file in _discover(directory):
        if version in already:
            continue

        sql = file.read_text(encoding="utf-8")
        conn.execute("BEGIN")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, file.name),
            )
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        applied.append(file.name)

    return applied


def initialise(path: Path | None = None) -> list[str]:
    """Create the data directories and bring the schema up to date.

    A campaign staged by an import is swapped in *first*, before any migration
    runs -- an archive from an older EzVTT arrives needing exactly the same
    upgrade a database from an older EzVTT does, and this is where that happens.
    """
    config.ensure_directories()

    if path is None:
        from . import campaign

        replaced = campaign.apply_pending()
        if replaced is not None:
            close()          # nothing may hold the file that was just swapped

    return migrate(path)


# --------------------------------------------------------------------------- #
# Settings helpers
# --------------------------------------------------------------------------- #

def get_setting(key: str, default: str = "", path: Path | None = None) -> str:
    row = connect(path).execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, path: Path | None = None) -> None:
    conn = connect(path)
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE
            SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value),
    )
    conn.commit()


def get_flag(key: str, default: bool = False, path: Path | None = None) -> bool:
    raw = get_setting(key, "1" if default else "0", path)
    return raw.strip().lower() in ("1", "true", "yes", "on")


def set_flag(key: str, value: bool, path: Path | None = None) -> None:
    set_setting(key, "1" if value else "0", path)


def is_first_run(path: Path | None = None) -> bool:
    """True until a master admin exists.

    Checked against the users table rather than only the ``first_run_complete``
    flag, so that deleting every account genuinely reopens the setup wizard
    instead of locking the owner out of their own install.
    """
    conn = connect(path)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchone()
    return row["n"] == 0
