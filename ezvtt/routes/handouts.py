"""The handout library: upload, rename, delete.

Showing and hiding go over the WebSocket, because they have to reach every
screen at once. Managing the library is ordinary request work that happens
between the moments that matter.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import config, handouts, media
from ..deps import require_gm
from ..hub import hub

log = logging.getLogger("ezvtt.handouts")

# Guards run for every route on this router, so a new endpoint cannot ship
# without one by forgetting to copy it in. See deps.py.
router = APIRouter(
    prefix="/api/handouts", tags=["handouts"], dependencies=[Depends(require_gm)]
)


@router.get("")
async def list_handouts(request: Request):
    return {"handouts": handouts.listing(), "showing": handouts.showing()}


@router.post("")
async def upload_handout(request: Request, file: UploadFile = File(...)):
    """Add an image to the library. Not shown until the GM says so."""
    data = await file.read()
    try:
        stored = media.store_upload(
            data, file.filename or "handout.png", config.HANDOUTS_DIR
        )
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    title = media.display_title(file.filename or "Handout")
    handout = handouts.add(stored, title)

    # Best effort: a missing thumbnail is cosmetic, never a failed upload.
    media.ensure_thumbnail(config.HANDOUTS_DIR / stored.filename)

    log.info("Handout %r added (%dx%d)", title, stored.width, stored.height)
    return {"handout": handout}


@router.patch("/{handout_id}")
async def rename_handout(request: Request, handout_id: int):
    body = await request.json()
    if not isinstance(body, dict) or "title" not in body:
        return JSONResponse({"error": "Expected a title."}, status_code=400)

    try:
        if not handouts.rename(handout_id, str(body["title"])):
            return JSONResponse({"error": "No such handout."}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # The title is drawn above the image, so a rename while it is up should
    # reach the table rather than waiting for the next time it is shown.
    await hub.broadcast_handout()
    return {"handout": handouts.get(handout_id)}


@router.delete("/{handout_id}")
async def delete_handout(request: Request, handout_id: int):
    filename = handouts.remove(handout_id)
    if filename is None:
        return JSONResponse({"error": "No such handout."}, status_code=404)

    path = config.HANDOUTS_DIR / filename
    try:
        thumb = media.thumbnail_path(path)
        path.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove handout file %s: %s", filename, exc)

    # Deleting the one on screen takes it off screen; handouts.remove has
    # already cleared the pointer, and this is what tells everybody.
    await hub.broadcast_handout()
    return {"deleted": handout_id}
