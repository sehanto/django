"""Content-Security-Policy: pages run only scripts and styles served from this site (the stylesheet
is built here, templates carry no inline scripts, handlers or styles), so injected markup can't run."""

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.header = "; ".join(f"{directive} {' '.join(sources)}" for directive, sources in settings.CONTENT_SECURITY_POLICY.items())

    def __call__(self, request):
        response = self.get_response(request)
        if settings.DEBUG and response.status_code >= 400:
            return response  # Django's debug pages style themselves inline; never shown in production
        if self.header and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.header
        return response
