from .selectors import resolve_membership
from .session import get_current_organization_id, set_current_organization


class CurrentOrganizationMiddleware:
    """Attach ``request.organization`` and ``request.membership``.

    The active organization is stored in the session; if it is missing or the
    user no longer belongs to it, the user's first membership is used.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = resolve_membership(user, get_current_organization_id(request))
            if membership is not None:
                request.membership = membership
                request.organization = membership.organization
                set_current_organization(request, membership.organization)
        return self.get_response(request)
