from django.views.generic import ListView

from apps.core.params import choice_param, text_param
from apps.tenants.mixins import ManageMixin

from .domain import AuditCategory
from .selectors import field_changes, organization_events, record_changes, trail_head, tracked_tables


class OrganizationActivityView(ManageMixin, ListView):
    """Owners and admins review who did what in their organization."""

    template_name = "audit/organization_activity.html"
    paginate_by = 50

    def get_queryset(self):
        return organization_events(self.organization, category=choice_param(self.request.GET, "category", AuditCategory.values))

    def get_context_data(self, **kwargs):
        organization_categories = [(c.value, c.label) for c in (AuditCategory.ORGANIZATION, AuditCategory.BILLING)]
        return super().get_context_data(categories=organization_categories, **kwargs)


class RecordChangesView(ManageMixin, ListView):
    """The audit trail: every record created, changed or deleted, by whom and when."""

    template_name = "audit/record_changes.html"
    paginate_by = 50

    def get_queryset(self):
        self.tables = tracked_tables()
        self.table = choice_param(self.request.GET, "table", self.tables)
        self.record = text_param(self.request.GET, "record", max_length=64) if self.table else ""
        return record_changes(self.organization, table=self.table, record=self.record).select_related("actor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            tables=self.tables, table=self.table, record=self.record, head=trail_head(self.organization), **kwargs,
        )
        context["rows"] = [(change, self.tables.get(change.table_name, change.table_name), field_changes(change))
                           for change in context["object_list"]]
        return context
