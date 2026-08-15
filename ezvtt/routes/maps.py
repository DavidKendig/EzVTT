"""Map library: upload, rename, delete, and put on the table.

Uploads go through ``media.store_upload``, which validates by content rather
than trusting the filename or the browser's content type.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import config, fog, gridfind, media, state
from ..deps import require_gm
from ..hub import hub

log = logging.getLogger("ezvtt.maps")

# Guards run for every route on this router, so a new endpoint cannot ship
# without one by forgetting to copy it in. See deps.py.
router = APIRouter(
    prefix="/api/maps", tags=["maps"], dependencies=[Depends(require_gm)]
)


@router.get("")
async def list_maps(request: Request):
    return {"maps": state.list_maps()}


@router.post("")
async def upload_map(request: Request, file: UploadFile = File(...)):
    """Accept a battlemap and put it straight on the table.

    Uploading is the first thing a GM does and the table should not need a
    second step, so the new map becomes the active scene immediately. If they
    were only adding to the library they can switch back in one click.
    """
    data = await file.read()
    try:
        stored = media.store_upload(data, file.filename or "map.png", config.MAPS_DIR)
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    name = media.display_title(file.filename or "Untitled map")
    map_id = state.create_map(stored, name)
    path = config.MAPS_DIR / stored.filename

    # Best effort: a missing thumbnail is cosmetic, never a failed upload.
    media.ensure_thumbnail(path)

    # Read the grid off the artwork before anyone sees the map, so the common
    # case is that it is already right. Off the event loop: it is tens of
    # milliseconds on a battlemap and a fifth of a second on a large one, and
    # every other client is waiting on this loop. A map with no drawn grid
    # keeps create_map's 30-square guess.
    guess = await asyncio.to_thread(gridfind.detect, path)
    if guess is not None:
        state.update_grid(map_id, **guess.as_changes())
        log.info("Grid detected on %r: %.1f px (confidence %.2f)",
                 name, guess.size_px, guess.confidence)

    scene_id = state.scene_for_map(map_id)
    state.activate_scene(scene_id)

    log.info("Map %r uploaded (%dx%d)", name, stored.width, stored.height)
    await hub.broadcast_state()

    return {"map": state.get_map(map_id), "detected": guess is not None}


@router.post("/{map_id}/detect-grid")
async def detect_grid(request: Request, map_id: int):
    """Re-read the grid from the artwork, for a map already in the library.

    The upload path does this automatically; this is the button for a map that
    was added before, or one whose grid has since been dragged about.
    """
    existing = state.get_map(map_id)
    if existing is None:
        return JSONResponse({"error": "No such map."}, status_code=404)

    filename = existing["url"].rsplit("/", 1)[-1]
    guess = await asyncio.to_thread(gridfind.detect, config.MAPS_DIR / filename)
    if guess is None:
        # Not an error. Plenty of good battlemaps have no grid drawn on them,
        # and saying so is more use than a failure.
        return {"detected": False, "map": existing}

    state.update_grid(map_id, **guess.as_changes())
    await hub.broadcast_state()
    return {
        "detected": True,
        "confidence": guess.confidence,
        "map": state.get_map(map_id),
    }


@router.patch("/{map_id}")
async def update_map(request: Request, map_id: int):
    """Rename a map, or adjust its grid.

    The grid is normally driven over the WebSocket while the slider moves; this
    exists for one-off changes and for clients without a live socket.
    """
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    try:
        if "name" in body and not state.rename_map(map_id, str(body["name"])):
            return JSONResponse({"error": "No such map."}, status_code=404)

        grid_changes = {k: v for k, v in body.items() if k != "name"}
        if grid_changes and state.update_grid(map_id, **grid_changes) is None:
            return JSONResponse({"error": "No such map."}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await hub.broadcast_state()
    return {"map": state.get_map(map_id)}


@router.delete("/{map_id}")
async def delete_map(request: Request, map_id: int):
    # Collected before the delete: the scenes cascade with the map, and their
    # composites on disk have no row left to find them by afterwards.
    scene_ids = state.scene_ids_for_map(map_id)

    filename = state.delete_map(map_id)
    if filename is None:
        return JSONResponse({"error": "No such map."}, status_code=404)

    for scene_id in scene_ids:
        fog.clear_composites(scene_id)

    # The database row is the source of truth, so the file is removed after it.
    # A leftover file wastes disk; a leftover row would show a broken map.
    path = config.MAPS_DIR / filename
    try:
        thumb = media.thumbnail_path(path)
        path.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove map file %s: %s", filename, exc)

    await hub.broadcast_state()
    return {"deleted": map_id}


@router.post("/{map_id}/activate")
async def activate_map(request: Request, map_id: int):
    if state.get_map(map_id) is None:
        return JSONResponse({"error": "No such map."}, status_code=404)

    state.activate_scene(state.scene_for_map(map_id))
    await hub.broadcast_state()
    return {"active_map_id": map_id}
