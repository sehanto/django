"""A form submitted twice (a double click, a resend after a slow response) is processed once.

Each rendered form carries a freshly masked CSRF token, so the same submission of the same form
has exactly the same fields, while two separate renders never do. The first submission of a
signed-in user is remembered for ``WINDOW_SECONDS`` in the shared cache: while it runs, a duplicate
goes back to the form with a notice; once it has succeeded (a redirect, as every write view
answers), a duplicate is sent to the same place without running again. A submission that failed
(the form shown again with errors) is forgotten, so it may be corrected and sent. The API has its
own Idempotency-Key (apps/api/idempotency.py).
"""

import hashlib

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

WINDOW_SECONDS = 60
PENDING = "pending"
TOKEN_FIELD = "csrfmiddlewaretoken"


def submission_key(request) -> str:
    fields = sorted((name, value) for name, values in request.POST.lists() for value in values)
    files = sorted((name, f.name, f.size) for name, items in request.FILES.lists() for f in items)
    identity = f"{request.user.pk}:{request.session.session_key}:{request.path}:{fields!r}:{files!r}"
    return "submission:" + hashlib.sha256(identity.encode()).hexdigest()


class DuplicateSubmissionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST" or not request.user.is_authenticated or TOKEN_FIELD not in request.POST:
            return self.get_response(request)  # only a rendered form carries its (masked) token
        key = submission_key(request)
        if not cache.add(key, PENDING, WINDOW_SECONDS):
            return self._duplicate(request, cache.get(key))
        try:
            response = self.get_response(request)
        except BaseException:
            cache.delete(key)
            raise
        if response.status_code in (301, 302, 303) and response.get("Location"):
            cache.set(key, response["Location"], WINDOW_SECONDS)  # done: a repeat goes where this one went
        else:
            cache.delete(key)  # nothing was recorded: it may be sent again
        return response

    @staticmethod
    def _duplicate(request, state):
        if state and state != PENDING:
            return HttpResponseRedirect(state)
        messages.info(request, _("This form was already sent and is still being processed. Check the result before sending it again."))
        back = request.META.get("HTTP_REFERER", "")
        safe = url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure())
        return HttpResponseRedirect(back if back and safe else "/")
