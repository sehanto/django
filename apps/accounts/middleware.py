from django.conf import settings
from django.shortcuts import redirect
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


class EmailVerificationMiddleware:
    """Signed-in users with an unconfirmed email only reach the verification pages.

    Allowed: the verification flow, sign-out, the language switch and the Django admin
    (staff-only, and superusers are verified on creation).
    """

    EXEMPT_ACCOUNT_VIEWS = {
        "verify_email", "verify_email_pending", "verify_email_resend", "verify_email_change", "logout", "set_language", "set_theme",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or user.is_email_verified:
            return None
        match = request.resolver_match
        if match and (match.namespace == "admin" or (match.namespace == "accounts" and match.url_name in self.EXEMPT_ACCOUNT_VIEWS)):
            return None
        return redirect("accounts:verify_email_pending")
