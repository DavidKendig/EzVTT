import mimetypes
from urllib.parse import quote, unquote

from django.http import (Http404, HttpResponse, HttpResponseBadRequest,
                         HttpResponseForbidden, JsonResponse)
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe

from . import media_store, wiki_content

GAME_NAME = "EzVTT"


def _ctx(request, **extra):
    """Shared template context. Identity comes only from the Java gateway."""
    ctx = {
        "game_name": GAME_NAME,
        # NOTE: these META keys stay UPPERCASE_EZVTT — Django normalizes any
        # incoming header (e.g. X-EzVTT-Role) to HTTP_X_EZVTT_ROLE, so this is
        # the framework's key, not the brand name.
        "role": request.META.get("HTTP_X_EZVTT_ROLE", "player"),
        "user": request.META.get("HTTP_X_EZVTT_USER", "anonymous"),
        "path": request.path,  # used by the debug role toggle's ?next=
    }
    ctx.update(extra)
    return ctx


def index(request):
    # Demo stats. Persistent counts will come from the DB later; the live
    # "players online" figure is fetched client-side from the Java /stats endpoint.
    return render(request, "index.html", _ctx(
        request,
        num_players=1,
        num_characters=1,
        num_wiki_pages=len(wiki_content.list_pages()),
        recent_sessions=[],
    ))


def play(request):
    ctx = _ctx(request)
    # The GM gets the image library so the play space can offer map/token pickers.
    if ctx["role"] == "gm":
        ctx["battlemaps"] = media_store.list_images("battlemaps")
        ctx["tokens"] = media_store.list_images("tokens")
    return render(request, "play.html", ctx)


def login(request):
    # The form POSTs to the Java gateway (/auth/login); this view only renders it.
    return render(request, "login.html", _ctx(request, error=request.GET.get("error")))


def register(request):
    # The form POSTs to the Java gateway (/auth/register); this view only renders it.
    return render(request, "register.html", _ctx(request, error=request.GET.get("error")))


def wiki(request, page=None):
    error = None
    # Sync on first use or when explicitly refreshed.
    if request.GET.get("refresh") or not wiki_content.is_synced():
        try:
            wiki_content.sync()
        except Exception as exc:  # network/parse failure shouldn't 500 the page
            error = f"Could not sync wiki content from GitHub: {exc}"

    page = page or "README"
    html = None if error else wiki_content.render_page(page)
    not_found = not error and html is None

    return render(request, "wiki.html", _ctx(
        request,
        wiki_repo=wiki_content.REPO,
        wiki_nav=mark_safe(wiki_content.nav_html(current=f"{page}.md")),
        wiki_html=mark_safe(html) if html else None,
        wiki_title=page.split("/")[-1],
        wiki_error=error,
        wiki_not_found=not_found,
    ))


def wiki_media(request, rel):
    target = wiki_content.safe_resolve(rel)
    if not target or not target.is_file():
        raise Http404("media not found")
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return HttpResponse(target.read_bytes(), content_type=ctype)


def admin(request):
    ctx = _ctx(request)
    # Image library is only meaningful for the GM; the template also gates it.
    if ctx["role"] == "gm":
        ctx["categories"] = [
            {"kind": "battlemaps", "label": "Battlemaps",
             "images": media_store.list_images("battlemaps")},
            {"kind": "tokens", "label": "Tokens",
             "images": media_store.list_images("tokens")},
        ]
        ctx["upload_ok"] = request.GET.get("ok")
        ctx["upload_error"] = request.GET.get("error")
    return render(request, "admin.html", ctx)


def admin_upload(request, kind):
    """Receive a battlemap/token image upload. GM-only, POST-only.

    Role comes from the X-EzVTT-Role header the Java gateway injects at the
    security boundary; Django trusts it (this app never faces the net).
    """
    if _ctx(request)["role"] != "gm":
        return HttpResponseForbidden("The image library is for the Game Master only.")
    if request.method != "POST":
        raise Http404()

    upload = request.FILES.get("image")
    if not upload:
        return redirect("/admin?error=" + quote("No file selected."))
    try:
        media_store.save_upload(kind, upload)
    except ValueError as exc:
        return redirect("/admin?error=" + quote(str(exc)))
    return redirect("/admin?ok=" + quote(f"Uploaded {upload.name}"))


def admin_grid(request):
    """Persist the grid size the GM fitted to a battlemap (POST: url, cols, rows).

    Called from the play space when the GM resizes the grid; the live broadcast
    goes over the WebSocket, this just saves the size to the image's metadata.
    """
    if _ctx(request)["role"] != "gm":
        return HttpResponseForbidden("GM only")
    if request.method != "POST":
        raise Http404()
    parts = request.POST.get("url", "").split("/")   # /media/<kind>/<source>/<name>
    if len(parts) != 5 or parts[1] != "media":
        return HttpResponseBadRequest("bad image url")
    try:
        grid = media_store.set_grid(parts[2], parts[3], unquote(parts[4]),
                                    request.POST.get("cols"), request.POST.get("rows"))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return JsonResponse(grid)


def media_file(request, kind, source, name):
    """Serve a stored battlemap/token image (sample or uploaded)."""
    target = media_store.resolve(kind, source, name)
    if not target:
        raise Http404("image not found")
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return HttpResponse(target.read_bytes(), content_type=ctype)
