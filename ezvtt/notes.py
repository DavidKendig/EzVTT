"""Player notes: a private pad each, and a shared board.

Visibility follows the pattern chat already proved -- one ``visible_to`` used
for both listing and fetching, so a note a player cannot see in the list cannot
be fetched by guessing its id either.

  private   the author alone
  public    everyone at the table
  gm        the author and the GMs -- for a question you want answered but not
            read aloud
"""

from __future__ import annotations

from typing import Any

from . import db

MAX_TITLE = 120
MAX_BODY = 20_000
VISIBILITIES = ("private", "public", "gm")


def _to_dict(row) -> dict[str, Any]:
    # Every query that reaches here joins the author name, so it is read
    # unconditionally. Guarding with `"author" in row` would not work anyway:
    # a sqlite3.Row tests membership against its *values*, not its keys.
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "author": row["author"],
        "title": row["title"],
        "body": row["body"],
        "visibility": row["visibility"],
        "updated_at": row["updated_at"],
    }


def visible_to(note: dict[str, Any], user_id: int | None, is_gm: bool) -> bool:
    """Whether one note may be shown to one viewer."""
    if note["user_id"] == user_id:
        return True
    if note["visibility"] == "public":
        return True
    if note["visibility"] == "gm":
        return is_gm
    return False


def list_notes(user_id: int | None, is_gm: bool) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT n.*, COALESCE(u.display_name, 'Unknown') AS author
        FROM notes n LEFT JOIN users u ON u.id = n.user_id
        ORDER BY n.updated_at DESC, n.id DESC
        """
    ).fetchall()
    notes = [_to_dict(row) for row in rows]
    return [note for note in notes if visible_to(note, user_id, is_gm)]


def get(note_id: int, user_id: int | None, is_gm: bool) -> dict[str, Any] | None:
    row = db.connect().execute(
        """
        SELECT n.*, COALESCE(u.display_name, 'Unknown') AS author
        FROM notes n LEFT JOIN users u ON u.id = n.user_id
        WHERE n.id = ?
        """,
        (note_id,),
    ).fetchone()
    if row is None:
        return None

    note = _to_dict(row)
    # Checked here as well as in the listing: without it, a player could read
    # any note by guessing an id.
    return note if visible_to(note, user_id, is_gm) else None


def create(user_id: int, title: str, body: str = "",
           visibility: str = "private") -> dict[str, Any]:
    if visibility not in VISIBILITIES:
        raise ValueError(f"Unknown visibility: {visibility}")

    title = (title or "").strip()[:MAX_TITLE] or "Untitled"
    conn = db.connect()
    cursor = conn.execute(
        """INSERT INTO notes (user_id, title, body, visibility)
           VALUES (?, ?, ?, ?)""",
        (user_id, title, (body or "")[:MAX_BODY], visibility),
    )
    conn.commit()
    return get(cursor.lastrowid, user_id, is_gm=False)


def update(note_id: int, user_id: int | None, is_gm: bool,
           **changes: Any) -> dict[str, Any] | None:
    """Edit a note. Only its author may change it -- a GM can read a shared
    note but not rewrite someone else's."""
    row = db.connect().execute(
        "SELECT user_id FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if row is None:
        return None
    if row["user_id"] != user_id:
        raise PermissionError("That is not your note.")

    fields: dict[str, Any] = {}
    if "title" in changes:
        fields["title"] = (str(changes["title"]) or "").strip()[:MAX_TITLE] or "Untitled"
    if "body" in changes:
        fields["body"] = str(changes["body"])[:MAX_BODY]
    if "visibility" in changes:
        visibility = str(changes["visibility"])
        if visibility not in VISIBILITIES:
            raise ValueError(f"Unknown visibility: {visibility}")
        fields["visibility"] = visibility

    if not fields:
        return get(note_id, user_id, is_gm)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn = db.connect()
    # Column names come from the fixed mapping above, never from client input.
    conn.execute(
        f"UPDATE notes SET {assignments}, updated_at = datetime('now') WHERE id = ?",  # noqa: S608
        (*fields.values(), note_id),
    )
    conn.commit()
    return get(note_id, user_id, is_gm)


def delete(note_id: int, user_id: int | None, is_gm: bool) -> bool:
    row = db.connect().execute(
        "SELECT user_id FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if row is None:
        return False
    # A GM may tidy up the shared board; otherwise only the author.
    if row["user_id"] != user_id and not is_gm:
        raise PermissionError("That is not your note.")

    conn = db.connect()
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return True
