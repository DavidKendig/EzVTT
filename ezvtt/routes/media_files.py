"""Serving images.

Every path here is resolved through ``media.resolve_within``, which refuses
anything that escapes its root. Filenames arrive from the URL, so this is the
one place a traversal attempt would land.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import fog, media
from ..deps import require_gm

router = APIRouter(prefix="/media", tags=["media"])

# Serving the original battlemap to a player would hand them everything fog is
# supposed to conceal, whatever the canvas draws on top. Players get a
# composite from /media/fog instead. See ADR-011.
GM_ONLY_KINDS = {"maps"}

# Stored filenames carry a random suffix and are never reused, so a served file
# is immutable for its lifetime and can be cached hard. Deleting a map changes
# the name, so this cannot serve a stale image.
_CACHE_CONTROL = "public, max-age=604800, immutable"


@router.get("/fog/{scene_id}")
async def serve_fog_composite(request: Request, scene_id: int, v: int = 0):
    """The player-visible map: the battlemap with unrevealed cells painted out.

    Built on demand and cached per fog version. This is the only map image a
    player is ever served.
    """
    # Compositing is Pillow work measured at ~800ms on a large map. Run inline
    # it froze the entire server for that long -- a /health probe during a
    # rebuild went from 6ms to 740ms, and every player and the display window
    # stalled together mid-brush-stroke. It has to leave the event loop.
    path = await asyncio.to_thread(fog.build_composite, scene_id)
    if path is None or not path.is_file():
        return JSONResponse({"error": "Not found."}, status_code=404)

    # Keyed by the ?v= fog version, so a reveal is picked up immediately while
    # an unchanged scene still caches hard.
    return FileResponse(path, headers={"Cache-Control": _CACHE_CONTROL})


@router.get("/thumbs/{kind}/{filename}")
async def serve_thumbnail(request: Request, kind: str, filename: str):
    if kind in GM_ONLY_KINDS:
        require_gm(request)

    """Serve a generated thumbnail, creating it on first request.

    Falls back to the full image rather than a broken tile -- a map with no
    thumbnail should still be recognisable in the library.
    """
    try:
        source = media.resolve_within(media.media_root(kind), filename)
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not source.is_file():
        return JSONResponse({"error": "Not found."}, status_code=404)

    # Also off the loop: opening the asset library requests sixty thumbnails at
    # once, and generating each one inline would serialise them all through the
    # event loop while every other client waited.
    thumb = await asyncio.to_thread(media.ensure_thumbnail, source)
    target = thumb if thumb and thumb.is_file() else source

    return FileResponse(target, headers={"Cache-Control": _CACHE_CONTROL})


@router.get("/{kind}/{filename}")
async def serve_media(request: Request, kind: str, filename: str):
    if kind in GM_ONLY_KINDS:
        require_gm(request)

    try:
        root = media.media_root(kind)
        path = media.resolve_within(root, filename)
    except media.MediaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not path.is_file():
        return JSONResponse({"error": "Not found."}, status_code=404)

    return FileResponse(path, headers={"Cache-Control": _CACHE_CONTROL})


@router.head("/{kind}/{filename}")
async def head_media(request: Request, kind: str, filename: str):
    if kind in GM_ONLY_KINDS:
        require_gm(request)

    try:
        path = media.resolve_within(media.media_root(kind), filename)
    except media.MediaError:
        return Response(status_code=400)
    return Response(status_code=200 if path.is_file() else 404)
