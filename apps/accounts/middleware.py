from django.conf import settings
from django.utils import translation

SUPPORTED_LANGUAGES = {code for code, _ in settings.LANGUAGES}


def language_for(request) -> str:
    """Signed-in users get their saved preference; visitors get the cookie or the default.

    The browser's Accept-Language header is deliberately ignored so the product
    opens in Indonesian unless someone explicitly chooses otherwise.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.language in SUPPORTED_LANGUAGES:
        return user.language
    cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    return cookie if cookie in SUPPORTED_LANGUAGES else settings.LANGUAGE_CODE


class UserLanguageMiddleware:
    """Activate the interface language for each request (runs after authentication)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        translation.activate(language_for(request))
        request.LANGUAGE_CODE = translation.get_language()
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", request.LANGUAGE_CODE)
        return response
