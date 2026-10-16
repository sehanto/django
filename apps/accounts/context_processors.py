from .models import Theme

THEME_COOKIE = "theme"  # for signed-out pages; signed in, the account's choice


def theme(request) -> dict:
    """``theme`` for <html data-theme>: the user's choice of light or dark pages, or the device's."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        chosen = user.theme
    else:
        chosen = request.COOKIES.get(THEME_COOKIE, "")
    return {"theme": chosen if chosen in Theme.values else Theme.SYSTEM, "themes": Theme}
