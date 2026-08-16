"""The campaign wiki and player notes.

Guarded at ``require_player``: everyone signed in may browse, and ``vault.py``
decides what each audience is actually shown. The role is passed to every call
rather than filtered afterwards, so a player's request never even opens a file
they may not read.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import notes as notes_module
from .. import vault
from ..deps import require_gm, require_player

log = logging.getLogger("ezvtt.wiki")

router = APIRouter(prefix="/api/vault", tags=["wiki"],
                   dependencies=[Depends(require_player)])


def _who(request: Request):
    principal = request.state.principal
    return principal, principal.is_gm


@router.get("/status")
async def status(request: Request):
    _, is_gm = _who(request)
    return {
        "configured": vault.is_configured(),
        "shared_folders": vault.allowed_dirs() if is_gm else [],
        # Players are told whether there is anything for them, not what is
        # being withheld.
        "has_content": bool(vault.tree(is_gm)),
    }


@router.get("/tree")
async def tree(request: Request):
    _, is_gm = _who(request)
    return {"tree": vault.tree(is_gm)}


@router.get("/note")
async def note(request: Request, path: str = ""):
    _, is_gm = _who(request)
    try:
        return vault.read(path, is_gm)
    except vault.VaultError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.get("/search")
async def search(request: Request, q: str = ""):
    _, is_gm = _who(request)
    return {"results": vault.search(q, is_gm)}


@router.get("/file")
async def file(request: Request, path: str = ""):
    """Serve an embedded image from the vault.

    Same allow-list as the notes: an image inside a GM-only folder is refused
    exactly as its note would be.
    """
    _, is_gm = _who(request)
    try:
        target = vault.resolve(path, is_gm)
    except vault.VaultError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    if not target.is_file() or target.suffix.lower() not in vault.EMBEDDABLE_SUFFIXES:
        return JSONResponse({"error": "Not found."}, status_code=404)

    return FileResponse(target, headers={"Cache-Control": "private, max-age=300"})


# --------------------------------------------------------------------------- #
# Configuration  (GM only)
# --------------------------------------------------------------------------- #

@router.get("/folders", dependencies=[Depends(require_gm)])
async def folders(request: Request):
    """Every folder in the vault, for the GM to tick."""
    root = vault.vault_root()
    if root is None:
        return {"root": None, "folders": [], "shared": []}

    found: list[str] = []
    for entry in sorted(root.rglob("*")):
        if len(found) >= 500:
            break
        if not entry.is_dir():
            continue
        if any(part.startswith(".") or part in vault.SKIP_DIRS for part in entry.parts):
            continue
        try:
            found.append(entry.relative_to(root).as_posix())
        except ValueError:
            continue

    return {"root": str(root), "folders": found, "shared": vault.allowed_dirs()}


@router.post("/config", dependencies=[Depends(require_gm)])
async def configure(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    try:
        if "path" in body:
            vault.set_vault_root(str(body["path"]))
        if "shared" in body:
            shared = body["shared"]
            if not isinstance(shared, list):
                return JSONResponse({"error": "shared must be a list."},
                                    status_code=400)
            vault.set_allowed_dirs([str(item) for item in shared])
    except vault.VaultError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    root = vault.vault_root()
    log.info("Vault set to %s; sharing %s", root, vault.allowed_dirs())
    return {"root": str(root) if root else None, "shared": vault.allowed_dirs()}


# --------------------------------------------------------------------------- #
# Player notes
# --------------------------------------------------------------------------- #

notes_router = APIRouter(prefix="/api/notes", tags=["notes"],
                         dependencies=[Depends(require_player)])


@notes_router.get("")
async def list_notes(request: Request):
    principal, is_gm = _who(request)
    return {"notes": notes_module.list_notes(principal.user_id, is_gm)}


@notes_router.post("")
async def create_note(request: Request):
    principal, _ = _who(request)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    try:
        note = notes_module.create(
            principal.user_id,
            str(body.get("title", "")),
            str(body.get("body", "")),
            str(body.get("visibility", "private")),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"note": note}


@notes_router.patch("/{note_id}")
async def update_note(request: Request, note_id: int):
    principal, is_gm = _who(request)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    changes = {k: v for k, v in body.items()
               if k in ("title", "body", "visibility")}
    try:
        note = notes_module.update(note_id, principal.user_id, is_gm, **changes)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if note is None:
        return JSONResponse({"error": "No such note."}, status_code=404)
    return {"note": note}


@notes_router.delete("/{note_id}")
async def delete_note(request: Request, note_id: int):
    principal, is_gm = _who(request)
    try:
        removed = notes_module.delete(note_id, principal.user_id, is_gm)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)

    if not removed:
        return JSONResponse({"error": "No such note."}, status_code=404)
    return {"deleted": note_id}


__all__ = ["router", "notes_router", "quote"]
