from django.views.generic import ListView

from apps.core.params import choice_param
from apps.tenants.mixins import ManageMixin

from .domain import AuditCategory
from .selectors import organization_events


class OrganizationActivityView(ManageMixin, ListView):
    """Owners and admins review who did what in their organization."""

    template_name = "audit/organization_activity.html"
    paginate_by = 50

    def get_queryset(self):
        return organization_events(self.organization, category=choice_param(self.request.GET, "category", AuditCategory.values))

    def get_context_data(self, **kwargs):
        organization_categories = [(c.value, c.label) for c in (AuditCategory.ORGANIZATION, AuditCategory.BILLING)]
        return super().get_context_data(categories=organization_categories, **kwargs)
