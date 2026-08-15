"""The WebSocket state hub.

Every open board -- the GM screen, the display window, each player's browser --
holds a connection here. Clients send *intents*; the hub checks the sender's
role, mutates the database, and broadcasts the result to everyone who should see
it. Clients never mutate shared state directly.

That is the whole reason fog of war and dice can be trusted later: authority
lives on this side of the socket. See ADR-004.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from . import state
from .auth import Principal

log = logging.getLogger("ezvtt.hub")

# A GM dragging the grid slider generates a message every animation frame. The
# hub is happy to keep up; this cap only stops a malfunctioning or hostile
# client from monopolising the event loop.
MAX_MESSAGE_BYTES = 64 * 1024

# How long to let a fog brush stroke settle before telling players about it.
# Short enough to feel immediate at the table, long enough that one sweep of
# the brush produces a single update rather than one per animation frame.
PLAYER_FOG_DEBOUNCE_SECONDS = 0.3


@dataclass
class Connection:
    socket: WebSocket
    principal: Principal
    # "gm" for the GM screen, "display" for the second monitor, "play" for a
    # player's browser. The display window follows the GM's view but has no
    # authority of its own.
    surface: str = "play"

    @property
    def is_gm(self) -> bool:
        return self.principal.is_gm


@dataclass
class Hub:
    connections: list[Connection] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Pending coalesced fog update for players; see _schedule_player_fog.
    _fog_task: asyncio.Task | None = field(default=None, repr=False)

    # ----------------------------------------------------------- membership --

    async def connect(self, socket: WebSocket, principal: Principal, surface: str) -> Connection:
        await socket.accept()
        connection = Connection(socket, principal, surface)
        async with self._lock:
            self.connections.append(connection)

        # A client that has just connected knows nothing. Send the whole table
        # rather than expecting it to reconstruct state from later deltas --
        # this is also what makes reconnect-after-sleep work correctly.
        await self.send(connection, {
            "type": "state",
            "you": {
                "role": principal.role.value,
                "display_name": principal.display_name,
                "is_gm": principal.is_gm,
                "via_bypass": principal.via_bypass,
            },
            "state": state.snapshot(for_gm=connection.is_gm),
        })

        # The display window has no chat panel, so it is not sent a log.
        if surface != "display" and principal.is_authenticated:
            from . import chat

            await self.send(connection, {
                "type": "chat.history",
                "messages": chat.history(principal.user_id, principal.is_gm),
                "roster": chat.roster() if principal.is_gm else [],
            })

        await self.broadcast_presence()
        return connection

    async def disconnect(self, connection: Connection) -> None:
        async with self._lock:
            if connection in self.connections:
                self.connections.remove(connection)
        await self.broadcast_presence()

    # ------------------------------------------------------------ delivery ---

    async def send(self, connection: Connection, message: dict[str, Any]) -> bool:
        """Send to one client. Returns False if the connection is gone."""
        if connection.socket.client_state is not WebSocketState.CONNECTED:
            return False
        try:
            await connection.socket.send_json(message)
            return True
        except (RuntimeError, ConnectionError, asyncio.CancelledError):
            # A browser tab closing mid-send is routine, not an error worth
            # logging on every occurrence.
            return False

    async def deliver(self, envelopes: list[tuple[Connection, dict[str, Any]]]) -> None:
        """Send a batch of per-connection messages and reap whatever is dead.

        Every fan-out goes through here. Reaping used to live in ``broadcast``
        alone, so the four other broadcast paths silently accumulated sockets
        that had gone away without their handler's cleanup running -- inflating
        the presence counts shown to the table.
        """
        if not envelopes:
            return

        results = await asyncio.gather(
            *(self.send(connection, message) for connection, message in envelopes)
        )
        dead = [
            connection
            for (connection, _), ok in zip(envelopes, results, strict=True)
            if not ok
        ]

        if dead:
            async with self._lock:
                for connection in dead:
                    if connection in self.connections:
                        self.connections.remove(connection)

    async def broadcast(self, message: dict[str, Any], gm_only: bool = False) -> None:
        async with self._lock:
            targets = [c for c in self.connections if not gm_only or c.is_gm]
        await self.deliver([(c, message) for c in targets])

    async def broadcast_state(self) -> None:
        """Re-send the full table to everyone, each tailored to their role.

        GM and player snapshots differ in content, so this cannot be one message
        fanned out -- it is built per audience.
        """
        async with self._lock:
            targets = list(self.connections)

        gm_state = state.snapshot(for_gm=True)
        player_state = state.snapshot(for_gm=False)

        await self.deliver([
            (c, {"type": "state", "state": gm_state if c.is_gm else player_state})
            for c in targets
        ])

    async def broadcast_token(self, token: dict[str, Any] | None, kind: str) -> None:
        """Send a single token delta.

        Dragging a token generates a message per frame; resending the entire
        table each time would push a map, a library, and every other token down
        the socket sixty times a second.

        A hidden token goes to GMs only. Players are not told it exists -- not
        even that something changed -- because "a token you cannot see just
        moved" is itself information about the encounter.
        """
        if token is None:
            return

        await self.broadcast({"type": kind, "token": token}, gm_only=token["hidden"])

    async def broadcast_token_visibility(self, token: dict[str, Any]) -> None:
        """Handle a token being hidden or revealed.

        Players get an add or a remove rather than a change, because from their
        side the token has appeared or ceased to exist.
        """
        async with self._lock:
            gms = [c for c in self.connections if c.is_gm]
            players = [c for c in self.connections if not c.is_gm]

        if token["hidden"]:
            message = {"type": "token.removed", "token_id": token["id"]}
        else:
            message = {"type": "token.added", "token": token}

        await self.deliver(
            [(c, {"type": "token.changed", "token": token}) for c in gms]
            + [(c, message) for c in players]
        )

    async def broadcast_message(self, message: dict[str, Any]) -> None:
        """Deliver one chat message to exactly the people allowed to read it.

        Filtered per connection through ``chat.visible_to`` -- the same function
        that filters replayed history -- so a private roll cannot be delivered
        live to someone who would not be shown it on reconnect.

        The display window is skipped entirely: it is the shared table view and
        stays chrome-free.
        """
        from . import chat

        async with self._lock:
            targets = [c for c in self.connections if c.surface != "display"]

        envelope = {"type": "chat", "message": message}
        await self.deliver([
            (c, envelope)
            for c in targets
            if chat.visible_to(message, c.principal.user_id, c.is_gm)
        ])

    async def broadcast_fog(self, fog_state: dict[str, Any] | None) -> None:
        """Push a fog change.

        The two audiences get fundamentally different things, which is the whole
        design. A GM receives the mask and draws it as a translucent overlay
        over the real map. A player receives only a new image URL -- the mask
        never reaches them, and neither do the pixels behind it.
        """
        if fog_state is None:
            return

        async with self._lock:
            gms = [c for c in self.connections if c.is_gm]

        # The GM's own view must track the brush, so their (small) mask update
        # goes out on every stroke.
        if gms:
            await self.deliver([(c, {
                "type": "fog",
                "cols": fog_state["cols"],
                "rows": fog_state["rows"],
                "cells": fog_state["cells"],
                "version": fog_state["version"],
            }) for c in gms])

        # Players are coalesced. A brush drag fires an event per pointermove,
        # and each player update is a whole snapshot plus a fresh composite to
        # download -- which they cannot render at that rate anyway. Sending only
        # the settled result is the same information for a fraction of the
        # traffic.
        self._schedule_player_fog()

    def _schedule_player_fog(self) -> None:
        """Coalesce player-facing fog updates onto a trailing edge."""
        if self._fog_task is not None and not self._fog_task.done():
            self._fog_task.cancel()
        self._fog_task = asyncio.create_task(self._flush_player_fog())

    async def _flush_player_fog(self) -> None:
        try:
            await asyncio.sleep(PLAYER_FOG_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return  # superseded by a later stroke; that one will flush instead

        async with self._lock:
            players = [c for c in self.connections if not c.is_gm]
        if not players:
            return

        # Built once and shared: every player sees the same fog.
        player_state = state.snapshot(for_gm=False)
        await self.deliver([
            (c, {"type": "state", "state": player_state}) for c in players
        ])

    async def broadcast_initiative(self, scene_id: int) -> None:
        """Push the turn order.

        Built twice, once per audience, for the same reason snapshots are: a
        concealed entry must be absent from a player's copy rather than filtered
        out of it after the fact. The display window authenticates as the GM and
        therefore receives the GM's copy -- it drops concealed entries itself,
        exactly as it already does with hidden tokens.
        """
        from . import initiative

        async with self._lock:
            targets = list(self.connections)
        if not targets:
            return

        gm_view = initiative.get(scene_id, for_gm=True)
        player_view = initiative.get(scene_id, for_gm=False)

        await self.deliver([
            (c, {"type": "initiative",
                 "initiative": gm_view if c.is_gm else player_view})
            for c in targets
        ])

    async def broadcast_presence(self) -> None:
        async with self._lock:
            gms = sum(1 for c in self.connections if c.is_gm and c.surface != "display")
            displays = sum(1 for c in self.connections if c.surface == "display")
            players = sum(1 for c in self.connections if not c.is_gm and c.surface != "display")

        await self.broadcast({
            "type": "presence",
            "gms": gms,
            "players": players,
            "displays": displays,
        })

    # ------------------------------------------------------------- intents ---

    async def handle(self, connection: Connection, message: Any) -> None:
        """Process one client message.

        Every branch that mutates state checks the sender's role first. A client
        claiming to be a GM in the message body is ignored -- authority comes
        from the session resolved at connect time, never from the payload.
        """
        if not isinstance(message, dict):
            return await self.send(connection, _error("Malformed message."))

        intent = message.get("type")
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            return await self.send(connection, _error("Malformed payload."))

        if intent == "ping":
            return await self.send(connection, {"type": "pong"})

        if intent == "resync":
            return await self.send(connection, {
                "type": "state",
                "state": state.snapshot(for_gm=connection.is_gm),
            })

        # Chat is the first thing any signed-in person may do, not just the GM.
        # Anonymous is still excluded: an unauthenticated socket must not be
        # able to put words in front of the table.
        table_handler = _TABLE_INTENTS.get(intent)
        if table_handler is not None:
            if not connection.principal.is_authenticated:
                return await self.send(connection, _error("Sign in to do that."))
            try:
                await table_handler(self, connection, payload)
            except (ValueError, KeyError, TypeError) as exc:
                await self.send(connection, _error(str(exc)))
            return

        handler = _GM_INTENTS.get(intent)
        if handler is None:
            return await self.send(connection, _error(f"Unknown action: {intent}"))

        if not connection.is_gm:
            log.warning(
                "Refused %s from %s (role %s)",
                intent, connection.principal.username, connection.principal.role.value,
            )
            return await self.send(connection, _error("Only the GM can do that."))

        try:
            await handler(self, connection, payload)
        except (ValueError, KeyError, TypeError) as exc:
            await self.send(connection, _error(str(exc)))


# --------------------------------------------------------------------------- #
# GM intents
# --------------------------------------------------------------------------- #

async def _set_grid(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Live grid adjustment. Fires continuously while the slider is dragged.

    Persisted on every change rather than on release: the requirement is that
    closing EzVTT mid-session and reopening restores the table exactly, and a
    debounce would lose the last adjustment on a crash.
    """
    map_id = payload.get("map_id")
    if not isinstance(map_id, int):
        raise ValueError("map_id is required.")

    changes = {k: v for k, v in payload.items() if k != "map_id"}
    updated = state.update_grid(map_id, **changes)
    if updated is None:
        raise ValueError("That map no longer exists.")

    # Sent to everyone including the sender, so the GM's own view is driven by
    # the server's clamped value rather than the raw slider position. Without
    # that, dragging past a limit lets the local view drift from the truth.
    await hub.broadcast({"type": "grid", "map_id": map_id, "grid": updated["grid"]})


async def _activate_scene(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Put a scene on the table.

    Takes a ``scene_id`` from the scene list, or a ``map_id`` from the map
    library -- clicking a map means "whichever of its scenes I was last
    running", which is what ``scene_for_map`` resolves.
    """
    scene_id = payload.get("scene_id")
    if scene_id is None:
        map_id = payload.get("map_id")
        if not isinstance(map_id, int):
            raise ValueError("scene_id or map_id is required.")
        scene_id = state.scene_for_map(map_id)
    elif not isinstance(scene_id, int):
        raise ValueError("scene_id must be a number.")

    if not state.activate_scene(scene_id):
        raise ValueError("That scene no longer exists.")

    await hub.broadcast_state()


async def _clear_table(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Take the current map off the table without deleting anything."""
    from . import db

    conn = db.connect()
    conn.execute("UPDATE scenes SET is_active = 0 WHERE is_active = 1")
    conn.commit()
    await hub.broadcast_state()


# --------------------------------------------------------------------------- #
# Token intents
# --------------------------------------------------------------------------- #

def _require_scene() -> int:
    scene = state.active_scene()
    if scene is None:
        raise ValueError("Put a map on the table first.")
    return scene["id"]


async def _place_token(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    asset_id = payload.get("asset_id")
    if not isinstance(asset_id, int):
        raise ValueError("asset_id is required.")

    token = state.place_token(
        _require_scene(),
        asset_id,
        float(payload.get("x", 0)),
        float(payload.get("y", 0)),
        str(payload.get("layer", "object")),
    )
    await hub.broadcast_token(token, "token.added")


async def _update_token(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Move, resize, rotate, relabel, hide, or lock a token.

    Fires continuously while a token is dragged. Each change is written through
    rather than debounced: closing EzVTT mid-drag and reopening must restore the
    table as it looked, and a debounce would lose the last position on a crash.
    """
    token_id = payload.get("token_id")
    if not isinstance(token_id, int):
        raise ValueError("token_id is required.")

    before = state.get_token(token_id)
    if before is None:
        raise ValueError("That token no longer exists.")

    changes = {k: v for k, v in payload.items() if k != "token_id"}
    token = state.update_token(token_id, **changes)
    if token is None:
        raise ValueError("That token no longer exists.")

    # Revealing or hiding changes who may know the token exists at all, so it
    # cannot be sent as an ordinary delta -- players who should no longer see it
    # need a removal, and players newly allowed to see it need the whole token.
    if before["hidden"] != token["hidden"]:
        from . import initiative

        # The tracker follows the token. Otherwise a GM who hides the ambush
        # again leaves its name sitting in the players' turn order.
        scene_id = initiative.sync_token_visibility(token_id, token["hidden"])
        await hub.broadcast_token_visibility(token)
        if scene_id is not None:
            await hub.broadcast_initiative(scene_id)
    else:
        await hub.broadcast_token(token, "token.changed")


async def _delete_token(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    token_id = payload.get("token_id")
    if not isinstance(token_id, int):
        raise ValueError("token_id is required.")

    before = state.get_token(token_id)
    if not state.delete_token(token_id):
        raise ValueError("That token no longer exists.")

    await hub.broadcast({"type": "token.removed", "token_id": token_id})

    # The token's initiative row cascaded away with it -- most often because the
    # creature whose turn it was just died. Resend the order so the table sees
    # the turn hand on rather than a tracker pointing at nothing.
    await hub.broadcast_initiative(before["scene_id"])


async def _clear_tokens(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    state.clear_tokens(_require_scene())
    await hub.broadcast_state()


# --------------------------------------------------------------------------- #
# Fog intents
# --------------------------------------------------------------------------- #

async def _fog_paint(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Brush fog. Fires continuously while the GM drags."""
    from . import fog

    state_after = fog.paint(
        _require_scene(),
        float(payload.get("x", 0)),
        float(payload.get("y", 0)),
        float(payload.get("radius", 2)),
        bool(payload.get("revealed", True)),
    )
    await hub.broadcast_fog(state_after)


async def _fog_rect(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import fog

    state_after = fog.paint_rect(
        _require_scene(),
        float(payload.get("x0", 0)), float(payload.get("y0", 0)),
        float(payload.get("x1", 0)), float(payload.get("y1", 0)),
        bool(payload.get("revealed", True)),
    )
    await hub.broadcast_fog(state_after)


async def _fog_all(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import fog

    state_after = fog.set_all(_require_scene(), bool(payload.get("revealed", True)))
    await hub.broadcast_fog(state_after)


# --------------------------------------------------------------------------- #
# Initiative intents
# --------------------------------------------------------------------------- #

def _entry_id(payload: dict[str, Any]) -> int:
    entry_id = payload.get("entry_id")
    if not isinstance(entry_id, int):
        raise ValueError("entry_id is required.")
    return entry_id


async def _initiative_add(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Add named entries, tokens, or both."""
    from . import initiative

    scene_id = _require_scene()

    token_ids = payload.get("token_ids")
    if isinstance(token_ids, list):
        initiative.add_tokens(scene_id, [t for t in token_ids if isinstance(t, int)])

    label = payload.get("label")
    if isinstance(label, str) and label.strip():
        initiative.add(
            scene_id, label,
            value=float(payload.get("value", 0)),
            modifier=int(payload.get("modifier", 0)),
            hidden=bool(payload.get("hidden", False)),
        )

    await hub.broadcast_initiative(scene_id)


async def _initiative_update(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    entry_id = _entry_id(payload)
    scene_id = initiative.scene_of(entry_id)
    if scene_id is None:
        raise ValueError("That entry is no longer in the order.")

    changes = {k: v for k, v in payload.items() if k != "entry_id"}
    initiative.update(entry_id, **changes)
    await hub.broadcast_initiative(scene_id)


async def _initiative_remove(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    entry_id = _entry_id(payload)
    scene_id = initiative.scene_of(entry_id)
    if scene_id is None or not initiative.remove(entry_id):
        raise ValueError("That entry is no longer in the order.")

    await hub.broadcast_initiative(scene_id)


async def _initiative_clear(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    scene_id = _require_scene()
    initiative.clear(scene_id)
    await hub.broadcast_initiative(scene_id)


async def _initiative_roll(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Roll d20 + modifier. One entry, or the whole order.

    Only the request crosses the socket; the dice are thrown here. There is
    nowhere in this payload to put a result. See ADR-004.
    """
    from . import initiative

    scene_id = _require_scene()
    entry_id = payload.get("entry_id")
    initiative.roll(scene_id, entry_id if isinstance(entry_id, int) else None)
    await hub.broadcast_initiative(scene_id)


async def _initiative_start(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    scene_id = _require_scene()
    initiative.start(scene_id)
    await hub.broadcast_initiative(scene_id)


async def _initiative_stop(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    scene_id = _require_scene()
    initiative.stop(scene_id)
    await hub.broadcast_initiative(scene_id)


async def _initiative_advance(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    scene_id = _require_scene()
    initiative.advance(scene_id, int(payload.get("delta", 1)))
    await hub.broadcast_initiative(scene_id)


async def _initiative_jump(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import initiative

    scene_id = _require_scene()
    initiative.jump(scene_id, _entry_id(payload))
    await hub.broadcast_initiative(scene_id)


# --------------------------------------------------------------------------- #
# Chat intents  (any signed-in person)
# --------------------------------------------------------------------------- #

async def _chat_say(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import chat

    message = chat.say(
        connection.principal.user_id,
        connection.principal.display_name,
        str(payload.get("text", "")),
    )
    await hub.broadcast_message(message)


async def _chat_roll(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    """Roll dice.

    Only notation crosses the socket. The result is computed here, so a client
    cannot report its own -- there is nowhere in this payload to put one.
    """
    from . import chat
    from .dice import DiceError

    try:
        message = chat.roll(
            connection.principal.user_id,
            connection.principal.display_name,
            str(payload.get("notation", "")),
            private=bool(payload.get("private", False)),
        )
    except DiceError as exc:
        # Not broadcast: a typo is between the roller and their own screen.
        return await hub.send(connection, _error(str(exc)))

    await hub.broadcast_message(message)


async def _chat_whisper(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import chat

    target = payload.get("target_user_id")
    if not isinstance(target, int):
        raise ValueError("Choose someone to whisper to.")

    message = chat.whisper(
        connection.principal.user_id,
        connection.principal.display_name,
        target,
        str(payload.get("text", "")),
    )
    await hub.broadcast_message(message)


async def _chat_history(hub: Hub, connection: Connection, payload: dict[str, Any]) -> None:
    from . import chat

    await hub.send(connection, {
        "type": "chat.history",
        "messages": chat.history(connection.principal.user_id, connection.is_gm),
        "roster": chat.roster() if connection.is_gm else [],
    })


_TABLE_INTENTS = {
    "chat.say": _chat_say,
    "chat.roll": _chat_roll,
    "chat.whisper": _chat_whisper,
    "chat.history": _chat_history,
}


_GM_INTENTS = {
    "grid.set": _set_grid,
    "scene.activate": _activate_scene,
    "table.clear": _clear_table,
    "token.place": _place_token,
    "token.update": _update_token,
    "token.delete": _delete_token,
    "tokens.clear": _clear_tokens,
    "fog.paint": _fog_paint,
    "fog.rect": _fog_rect,
    "fog.all": _fog_all,
    "initiative.add": _initiative_add,
    "initiative.update": _initiative_update,
    "initiative.remove": _initiative_remove,
    "initiative.clear": _initiative_clear,
    "initiative.roll": _initiative_roll,
    "initiative.start": _initiative_start,
    "initiative.stop": _initiative_stop,
    "initiative.advance": _initiative_advance,
    "initiative.jump": _initiative_jump,
}


def _error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}


# One hub per process. The application is a single uvicorn worker by design --
# multiple workers would each hold their own hub and silently split the table.
hub = Hub()
