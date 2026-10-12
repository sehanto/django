from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers

SUPPORTED_LANGUAGES = {code for code, _ in settings.LANGUAGES}


def language_for(request) -> str:
    """Pick the interface language, most explicit signal first.

    1. A signed-in user's saved preference.
    2. The language cookie (an explicit choice made while signed out).
    3. The browser's Accept-Language header, as a first guess for new visitors.
    4. The default, Indonesian.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.language in SUPPORTED_LANGUAGES:
        return user.language
    # Handles steps 2-4; only languages listed in settings.LANGUAGES are returned.
    return translation.get_language_from_request(request)


class UserLanguageMiddleware:
    """Activate the interface language for each request (runs after authentication)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        translation.activate(language_for(request))
        request.LANGUAGE_CODE = translation.get_language()
        response = self.get_response(request)
        # The same URL renders differently per language, so shared caches must key on these.
        patch_vary_headers(response, ("Accept-Language", "Cookie"))
        response.headers.setdefault("Content-Language", request.LANGUAGE_CODE)
        return response
