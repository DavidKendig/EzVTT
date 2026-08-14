"""Asset library: search, upload, rename, resize, delete.

"Resize" changes how many grid squares an asset occupies. For bundled Cartos
artwork that is metadata only -- the image file is never touched, and the
renderer scales the unmodified original. See ADR-005.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import assets, config, media
from ..deps import require_gm
from ..hub import hub

log = logging.getLogger("ezvtt.assets")

# Guards run for every route on this router. See deps.py.
router = APIRouter(
    prefix="/api/assets", tags=["assets"], dependencies=[Depends(require_gm)]
)


@router.get("")
async def search_assets(
    request: Request,
    q: str = "",
    category: str = "",
    limit: int = 120,
    offset: int = 0,
):
    return assets.search(q, category, limit, offset)


@router.get("/categories")
async def list_categories(request: Request):
    return {"categories": assets.category_counts()}


@router.post("")
async def upload_asset(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    original = file.filename or "asset.png"
    try:
        stored = media.store_upload(data, original, config.UPLOADS_DIR)
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # An uploaded file may carry the same "_2x1" convention as the bundle, so
    # honour it -- a GM exporting from the same source gets sizing for free.
    from ..grid import display_name, parse_footprint

    name = display_name(original)
    grid_w, grid_h = parse_footprint(original)

    from .. import db

    conn = db.connect()
    cursor = conn.execute(
        """
        INSERT INTO assets (name, filename, source, grid_w, grid_h, category)
        VALUES (?, ?, 'user', ?, ?, ?)
        """,
        (name, stored.filename, grid_w, grid_h, assets.categorise(name)),
    )
    conn.commit()

    media.ensure_thumbnail(config.UPLOADS_DIR / stored.filename)
    return {"asset": assets.get_asset(cursor.lastrowid)}


@router.patch("/{asset_id}")
async def update_asset(request: Request, asset_id: int):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    try:
        if "name" in body and not assets.rename_asset(asset_id, str(body["name"])):
            return JSONResponse({"error": "No such asset."}, status_code=404)

        if "grid_w" in body or "grid_h" in body:
            current = assets.get_asset(asset_id)
            if current is None:
                return JSONResponse({"error": "No such asset."}, status_code=404)
            assets.resize_asset(
                asset_id,
                body.get("grid_w", current["grid_w"]),
                body.get("grid_h", current["grid_h"]),
            )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return {"asset": assets.get_asset(asset_id)}


@router.delete("/{asset_id}")
async def delete_asset(request: Request, asset_id: int):
    try:
        filename = assets.delete_asset(asset_id)
    except ValueError as exc:
        # Bundled artwork is not deletable -- the next import would restore it.
        return JSONResponse({"error": str(exc)}, status_code=400)

    if filename is None:
        return JSONResponse({"error": "No such asset."}, status_code=404)

    path = config.UPLOADS_DIR / filename
    try:
        media.thumbnail_path(path).unlink(missing_ok=True)
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove asset file %s: %s", filename, exc)

    # Tokens referencing it are left in place with a null asset; the scene keeps
    # its layout rather than silently losing pieces mid-session.
    await hub.broadcast_state()
    return {"deleted": asset_id}
