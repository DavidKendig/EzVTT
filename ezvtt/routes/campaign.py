"""Campaign export and import.

Admin-only, not GM-only: an export is every account in the campaign and every
map in the library, and an import replaces all of it. That is a different kind
of act from putting a monster on a board.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .. import campaign, db
from ..deps import require_admin
from .accounts import _check_csrf

log = logging.getLogger("ezvtt.campaign")

router = APIRouter(
    prefix="/api/campaign", tags=["campaign"], dependencies=[Depends(require_admin)]
)

# Uploads are read into a temporary file rather than memory: a campaign with a
# hundred battlemaps in it is not something to hold in RAM twice.
CHUNK = 1024 * 1024


def _archive_name() -> str:
    name = db.get_setting("campaign_name", "campaign")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "campaign"
    return f"ezvtt-{slug}-{time.strftime('%Y%m%d-%H%M%S')}.zip"


@router.get("/export")
async def export_campaign(request: Request, background: BackgroundTasks):
    """Download the whole campaign as one archive."""
    target = Path(tempfile.gettempdir()) / _archive_name()

    # Zipping a campaign is filesystem work measured in seconds on a library of
    # battlemaps. Inline it would stall every other client for the duration.
    await asyncio.to_thread(campaign.export_to, target)

    # Removed once the response has been sent; it is a copy, not the original.
    background.add_task(target.unlink, missing_ok=True)
    return FileResponse(
        target, filename=target.name, media_type="application/zip",
        background=background,
    )


@router.get("/import")
async def import_status(request: Request):
    return {"pending": campaign.pending()}


@router.post("/import")
async def import_campaign(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(""),
):
    """Stage an archive to be swapped in on the next start.

    Deliberately not applied here. Replacing a SQLite file that open
    connections are holding is how a database becomes corrupt, and there is no
    honest way to do it while the table is connected. See ADR-019.

    This is the most destructive thing in the program -- it replaces an entire
    campaign -- so it carries the CSRF token the admin forms use, rather than
    being a bare JSON endpoint a page on another site could post to.
    """
    if not _check_csrf(request, csrf_token):
        return JSONResponse({"error": "That form has expired. Reload and try again."},
                            status_code=403)

    staging = Path(tempfile.gettempdir()) / f"ezvtt-import-{time.time_ns()}.zip"
    try:
        with staging.open("wb") as sink:
            while chunk := await file.read(CHUNK):
                sink.write(chunk)

        info = await asyncio.to_thread(campaign.stage_import, staging)
    except campaign.CampaignError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        staging.unlink(missing_ok=True)

    log.warning("Campaign import staged by %s", request.state.principal.username)

    # Posted from the admin page's own form, so the answer is that page again.
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/admin?imported=1", status_code=303)

    return {
        "staged": True,
        "manifest": info,
        "message": "Restart EzVTT to finish the import.",
    }


@router.post("/import/cancel")
async def cancel_import(request: Request, csrf_token: str = Form("")):
    if not _check_csrf(request, csrf_token):
        return JSONResponse({"error": "That form has expired. Reload and try again."},
                            status_code=403)

    cancelled = campaign.cancel_pending()
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/admin", status_code=303)
    return {"cancelled": cancelled}
