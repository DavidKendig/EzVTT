"""Join details for the GM: what address to give players, and a QR for it.

The console banner already prints this at startup, but by the time players
arrive the GM is looking at the browser, not the terminal they launched from --
often on a laptop whose terminal is behind the EzVTT window.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from .. import net
from ..config import RunMode
from ..deps import require_gm

log = logging.getLogger("ezvtt.net")

router = APIRouter(prefix="/api/net", tags=["network"],
                   dependencies=[Depends(require_gm)])

# The public address rarely changes within a session and the lookup leaves the
# machine, so it is fetched once and reused.
_public_address: str | None = None
_public_checked = False


@router.get("/join")
async def join_details(request: Request):
    """Where players should point their browsers, with a scannable QR."""
    global _public_address, _public_checked

    settings = request.state.settings

    # Only in internet mode, and only once. Every other mode must work with no
    # route to the internet at all, so it is never called there.
    if settings.mode is RunMode.INTERNET and not _public_checked:
        _public_checked = True
        _public_address = await asyncio.to_thread(net.public_address)

    info = net.join_info(settings, public=_public_address)

    return {
        "mode": info.mode,
        "shareable": info.shareable,
        "url": info.url,
        "host": info.host,
        "port": info.port,
        "alternatives": info.alternatives,
        "note": info.note,
        # Inlined rather than a separate image request, so it renders with no
        # network round trip and no CDN.
        "qr_svg": net.qr_svg(info.url) if info.url else None,
    }
