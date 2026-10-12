SESSION_KEY = "current_organization_id"


def set_current_organization(request, organization) -> None:
    request.session[SESSION_KEY] = organization.pk


def get_current_organization_id(request):
    return request.session.get(SESSION_KEY)
