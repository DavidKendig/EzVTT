"""Migration runner and settings storage.

Every test uses a temporary database file so nothing here can touch a real
campaign in data/ezvtt.db.
"""

import sqlite3

import pytest

from ezvtt import config, db


@pytest.fixture
def temp_db(tmp_path):
    path = tmp_path / "test.db"
    db.migrate(path)
    yield path
    db.close()


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "fresh.db"

    first = db.migrate(path)
    assert "001_init.sql" in first

    # Re-running must be a no-op; setup.sh runs this on every invocation.
    second = db.migrate(path)
    assert second == []
    db.close()


def test_expected_tables_exist(temp_db):
    conn = db.connect(temp_db)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for expected in (
        "users", "sessions", "settings", "maps", "scenes",
        "assets", "tokens", "fog", "notes", "chat_log", "initiative",
    ):
        assert expected in tables


def test_foreign_keys_are_enforced(temp_db):
    """Without PRAGMA foreign_keys the schema's CASCADE clauses do nothing."""
    conn = db.connect(temp_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tokens (scene_id, x, y) VALUES (?, ?, ?)", (9999, 0, 0)
        )
        conn.commit()


def test_deleting_a_scene_cascades_to_its_tokens(temp_db):
    conn = db.connect(temp_db)
    conn.execute("INSERT INTO scenes (id, name) VALUES (1, 'Test')")
    conn.execute("INSERT INTO tokens (scene_id, x, y) VALUES (1, 0, 0)")
    conn.commit()

    conn.execute("DELETE FROM scenes WHERE id = 1")
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM tokens").fetchone()["n"] == 0


def test_only_one_scene_can_be_active(temp_db):
    """Enforced by a partial unique index rather than by every write path."""
    conn = db.connect(temp_db)
    conn.execute("INSERT INTO scenes (name, is_active) VALUES ('A', 1)")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scenes (name, is_active) VALUES ('B', 1)")
        conn.commit()

    conn.rollback()
    # Any number of inactive scenes is fine.
    conn.execute("INSERT INTO scenes (name, is_active) VALUES ('B', 0)")
    conn.execute("INSERT INTO scenes (name, is_active) VALUES ('C', 0)")
    conn.commit()


def test_role_check_constraint_rejects_unknown_roles(temp_db):
    conn = db.connect(temp_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO users
               (username, display_name, pw_hash, pw_salt, pw_n, pw_r, pw_p, role)
               VALUES ('x', 'X', 'h', 's', 16384, 8, 1, 'superuser')"""
        )
        conn.commit()


def test_usernames_are_case_insensitively_unique(temp_db):
    """'Gary' and 'gary' must not be two accounts."""
    conn = db.connect(temp_db)
    insert = """INSERT INTO users
                (username, display_name, pw_hash, pw_salt, pw_n, pw_r, pw_p, role)
                VALUES (?, 'X', 'h', 's', 16384, 8, 1, 'player')"""
    conn.execute(insert, ("Gary",))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("gary",))
        conn.commit()


def test_settings_round_trip_and_upsert(temp_db):
    db.set_setting("campaign_name", "Curse of Strahd", temp_db)
    assert db.get_setting("campaign_name", path=temp_db) == "Curse of Strahd"

    db.set_setting("campaign_name", "Tomb of Annihilation", temp_db)
    assert db.get_setting("campaign_name", path=temp_db) == "Tomb of Annihilation"


def test_missing_setting_returns_the_default(temp_db):
    assert db.get_setting("nonexistent", "fallback", temp_db) == "fallback"


@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nonsense", False),
])
def test_flag_parsing(temp_db, raw, expected):
    db.set_setting("a_flag", raw, temp_db)
    assert db.get_flag("a_flag", path=temp_db) is expected


def test_first_run_is_true_until_an_active_admin_exists(temp_db):
    assert db.is_first_run(temp_db) is True

    conn = db.connect(temp_db)
    conn.execute(
        """INSERT INTO users
           (username, display_name, pw_hash, pw_salt, pw_n, pw_r, pw_p, role)
           VALUES ('admin', 'Admin', 'h', 's', 16384, 8, 1, 'admin')"""
    )
    conn.commit()
    assert db.is_first_run(temp_db) is False

    # Deactivating the only admin must reopen the wizard rather than locking the
    # owner out of their own install.
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'admin'")
    conn.commit()
    assert db.is_first_run(temp_db) is True


def test_a_player_account_does_not_satisfy_first_run(temp_db):
    conn = db.connect(temp_db)
    conn.execute(
        """INSERT INTO users
           (username, display_name, pw_hash, pw_salt, pw_n, pw_r, pw_p, role)
           VALUES ('bob', 'Bob', 'h', 's', 16384, 8, 1, 'player')"""
    )
    conn.commit()
    assert db.is_first_run(temp_db) is True


def test_migration_filenames_must_be_ordered(tmp_path):
    """A misnamed migration would apply in an unpredictable order."""
    bad = tmp_path / "migrations"
    bad.mkdir()
    (bad / "init.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="NNN_name.sql"):
        db.migrate(tmp_path / "x.db", bad)
    db.close()


def test_duplicate_migration_versions_are_rejected(tmp_path):
    bad = tmp_path / "migrations"
    bad.mkdir()
    (bad / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (bad / "001_b.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate migration"):
        db.migrate(tmp_path / "y.db", bad)
    db.close()


def test_shipped_migrations_are_wellformed():
    """Guards against a future migration being added with a bad name."""
    from ezvtt.db import _discover

    found = _discover(config.MIGRATIONS_DIR)
    assert found, "no migrations found"
    assert found[0][0] == 1
