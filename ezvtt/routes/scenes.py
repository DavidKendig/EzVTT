"""Scenes: prep several encounters over the map library, switch between them.

A scene is a map plus its own tokens and its own fog. Switching is a WebSocket
intent (``scene.activate``) because it has to reach every open board at once;
creating, renaming, duplicating, and deleting live here, where they are ordinary
requests that happen between sessions rather than mid-combat.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import fog, state
from ..deps import require_gm
from ..hub import hub

log = logging.getLogger("ezvtt.scenes")

# Guards run for every route on this router, so a new endpoint cannot ship
# without one by forgetting to copy it in. See deps.py.
router = APIRouter(
    prefix="/api/scenes", tags=["scenes"], dependencies=[Depends(require_gm)]
)


@router.get("")
async def list_scenes(request: Request):
    return {"scenes": state.list_scenes()}


@router.post("")
async def create_scene(request: Request):
    """Prep a new, empty scene over an existing map.

    Not activated. A GM who adds a scene mid-session has not asked to put it in
    front of the table yet -- that is the click on the scene itself.
    """
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    map_id = body.get("map_id")
    if not isinstance(map_id, int):
        return JSONResponse({"error": "map_id is required."}, status_code=400)

    source = state.get_map(map_id)
    if source is None:
        return JSONResponse({"error": "No such map."}, status_code=404)

    try:
        scene_id = state.create_scene(map_id, str(body.get("name") or source["name"]))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await hub.broadcast_state()
    return {"scene": state.get_scene(scene_id)}


@router.post("/{scene_id}/duplicate")
async def duplicate_scene(request: Request, scene_id: int):
    new_id = state.duplicate_scene(scene_id)
    if new_id is None:
        return JSONResponse({"error": "No such scene."}, status_code=404)

    await hub.broadcast_state()
    return {"scene": state.get_scene(new_id)}


@router.patch("/{scene_id}")
async def rename_scene(request: Request, scene_id: int):
    body = await request.json()
    if not isinstance(body, dict) or "name" not in body:
        return JSONResponse({"error": "Expected a name."}, status_code=400)

    try:
        if not state.rename_scene(scene_id, str(body["name"])):
            return JSONResponse({"error": "No such scene."}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await hub.broadcast_state()
    return {"scene": state.get_scene(scene_id)}


@router.delete("/{scene_id}")
async def delete_scene(request: Request, scene_id: int):
    if not state.delete_scene(scene_id):
        return JSONResponse({"error": "No such scene."}, status_code=404)

    # The row is the source of truth, so the composites go after it. A leftover
    # file wastes disk; a leftover row would show a scene that cannot be opened.
    fog.clear_composites(scene_id)

    await hub.broadcast_state()
    return {"deleted": scene_id}
