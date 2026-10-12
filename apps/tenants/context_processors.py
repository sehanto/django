from .roles import Permission
from .selectors import organizations_for_user


def current_organization(request):
    membership = getattr(request, "membership", None)
    if membership is None:
        return {}
    return {
        "current_organization": request.organization,
        "current_membership": membership,
        "user_organizations": organizations_for_user(request.user),
        "can_write": membership.has_permission(Permission.WRITE),
        "can_manage": membership.has_permission(Permission.MANAGE),
    }
