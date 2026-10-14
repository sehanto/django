from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.params import choice_param, text_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .forms import ContactForm
from apps.regions.models import Province

from .models import Contact
from .selectors import contacts_for


class ContactListView(OrganizationMixin, ListView):
    template_name = "contacts/contact_list.html"
    paginate_by = 25

    def get_queryset(self):
        params = self.request.GET
        self.provinces = list(Province.objects.filter(is_active=True))
        province = choice_param(params, "province", [p.code for p in self.provinces])
        contacts = contacts_for(
            self.organization, query=text_param(params, "q"), kind=choice_param(params, "kind", Contact.Kind.values)
        ).select_related("village__district__regency")
        return contacts.filter(village__district__regency__province_id=province) if province else contacts

    def get_context_data(self, **kwargs):
        return super().get_context_data(kinds=Contact.Kind.choices, provinces=self.provinces, **kwargs)


class ContactFormMixin(WriteMixin):
    form_class = ContactForm
    template_name = "contacts/contact_form.html"
    success_url = reverse_lazy("contacts:list")

    def get_queryset(self):
        return contacts_for(self.organization)

    def form_valid(self, form):
        self.save(form.cleaned_data)
        messages.success(self.request, _("Contact saved."))
        return redirect(self.success_url)


class ContactCreateView(ContactFormMixin, CreateView):
    def get_initial(self):
        return {"kind": choice_param(self.request.GET, "kind", Contact.Kind.values, default=Contact.Kind.CUSTOMER)}

    def save(self, data):
        services.create_contact(organization=self.organization, **data)


class ContactUpdateView(ContactFormMixin, UpdateView):
    def save(self, data):
        services.update_contact(contact=self.object, **data)


class ContactDeleteView(WriteMixin, View):
    def post(self, request, pk):
        contact = get_object_or_404(contacts_for(self.organization), pk=pk)
        try:
            services.delete_contact(contact=contact)
            messages.success(request, _("Contact deleted."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("contacts:list")
