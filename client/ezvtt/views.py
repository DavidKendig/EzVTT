import mimetypes

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe

from . import wiki_content

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
    return render(request, "play.html", _ctx(request))


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
    return render(request, "admin.html", _ctx(request))
