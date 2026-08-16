"""Chat, dice results, and who is allowed to read them.

The audience rules live here rather than in the hub so that delivery and replay
cannot disagree: a message a player is not shown live must also be absent from
the history they receive on reconnect. Getting those two out of step is how
"private" rolls quietly stop being private.

  public    everyone at the table
  private   the roller and the GMs -- for a secret perception check
  whisper   the sender and the named recipient, nobody else
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import db
from .dice import DiceError, Roll, evaluate

log = logging.getLogger("ezvtt.chat")

# Kept per scene-less table rather than forever. A long campaign would otherwise
# accumulate an unbounded log that is replayed in full on every connect.
HISTORY_LIMIT = 500
REPLAY_LIMIT = 120
MAX_MESSAGE_LENGTH = 1000


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "author": row["author"],
        "kind": row["kind"],
        "body": row["body"],
        "roll": json.loads(row["roll_json"]) if row["roll_json"] else None,
        "target_user_id": row["target_user_id"],
        "private": bool(row["is_private"]),
        "at": row["created_at"],
    }


def visible_to(message: dict[str, Any], user_id: int | None, is_gm: bool) -> bool:
    """Whether one message may be shown to one viewer.

    The single source of truth for delivery *and* replay.
    """
    if message["kind"] == "whisper":
        # Deliberately not visible to GMs who are not a party to it. A GM can
        # see private rolls because they are the one asking for them; reading
        # other people's whispers is a different thing entirely.
        return user_id is not None and user_id in (
            message["user_id"], message["target_user_id"]
        )

    if message["private"]:
        return is_gm or (user_id is not None and user_id == message["user_id"])

    return True


def post(
    *,
    user_id: int | None,
    author: str,
    kind: str,
    body: str,
    roll: Roll | None = None,
    target_user_id: int | None = None,
    private: bool = False,
) -> dict[str, Any]:
    """Record a message and return it as a dict."""
    if kind not in ("chat", "roll", "whisper", "system"):
        raise ValueError(f"Unknown message kind: {kind}")

    conn = db.connect()
    cursor = conn.execute(
        """
        INSERT INTO chat_log
            (user_id, author, kind, body, roll_json, target_user_id, is_private)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, author[:60], kind, body[:MAX_MESSAGE_LENGTH],
         json.dumps(roll.to_dict()) if roll else None,
         target_user_id, 1 if private else 0),
    )
    conn.commit()

    _prune(conn)

    row = conn.execute(
        "SELECT * FROM chat_log WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_dict(row)


def _prune(conn) -> None:
    """Keep the log bounded, oldest first."""
    conn.execute(
        """
        DELETE FROM chat_log
        WHERE id NOT IN (
            SELECT id FROM chat_log ORDER BY id DESC LIMIT ?
        )
        """,
        (HISTORY_LIMIT,),
    )
    conn.commit()


def history(user_id: int | None, is_gm: bool, limit: int = REPLAY_LIMIT) -> list[dict]:
    """Recent messages this viewer is allowed to see, oldest first.

    Filtered through the same ``visible_to`` used for live delivery, so a
    reconnecting player cannot pick up a private roll they missed.
    """
    rows = db.connect().execute(
        "SELECT * FROM chat_log ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, HISTORY_LIMIT)),),
    ).fetchall()

    messages = [_row_to_dict(row) for row in rows]
    visible = [m for m in messages if visible_to(m, user_id, is_gm)]
    visible.reverse()
    return visible


def clear() -> int:
    conn = db.connect()
    cursor = conn.execute("DELETE FROM chat_log")
    conn.commit()
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Composing
# --------------------------------------------------------------------------- #

def say(user_id: int | None, author: str, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to say.")
    return post(user_id=user_id, author=author, kind="chat", body=text)


def roll(
    user_id: int | None, author: str, notation: str, private: bool = False
) -> dict[str, Any]:
    """Evaluate dice on the server and record the result.

    The caller supplies notation only. A client that sent its own total would
    be ignored -- there is no field here to put one in.
    """
    try:
        result = evaluate(notation)
    except DiceError:
        raise

    return post(
        user_id=user_id, author=author, kind="roll",
        body=result.notation, roll=result, private=private,
    )


def whisper(
    user_id: int | None, author: str, target_user_id: int, text: str
) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to say.")

    target = db.connect().execute(
        "SELECT id FROM users WHERE id = ? AND is_active = 1", (target_user_id,)
    ).fetchone()
    if target is None:
        raise ValueError("There is no such person at this table.")

    return post(user_id=user_id, author=author, kind="whisper",
                body=text, target_user_id=target_user_id, private=True)


def roster() -> list[dict[str, Any]]:
    """Who can be whispered to."""
    rows = db.connect().execute(
        """SELECT id, username, display_name, role FROM users
           WHERE is_active = 1 ORDER BY display_name COLLATE NOCASE"""
    ).fetchall()
    return [dict(row) for row in rows]
