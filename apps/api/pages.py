"""Managing the organization's API keys (owners and administrators)."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.exceptions import DomainError
from apps.core.forms import BaseForm
from apps.core.views import flash_domain_error
from apps.tenants.mixins import ManageMixin

from . import services
from .models import ApiKey
from .selectors import api_keys


class ApiKeyForm(BaseForm):
    name = forms.CharField(label=gettext_lazy("Name"), max_length=100, help_text=gettext_lazy("What it is for, e.g. the online shop."))


class ApiKeysView(ManageMixin, View):
    template_name = "api/keys.html"

    def render(self, request, form, secret=""):
        keys = api_keys(self.organization)
        return render(request, self.template_name, {"form": form, "keys": keys, "secret": secret})

    def get(self, request):
        return self.render(request, ApiKeyForm())

    def post(self, request):
        form = ApiKeyForm(request.POST)
        if not form.is_valid():
            return self.render(request, form)
        try:
            key, secret = services.create_key(organization=self.organization, user=request.user, name=form.cleaned_data["name"])
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("api_keys:list")
        audit.record(AuditAction.API_KEY_CREATED, request=request, organization=self.organization, target=key.name, prefix=key.prefix)
        return self.render(request, ApiKeyForm(), secret=secret)  # shown this once only


class RevokeApiKeyView(ManageMixin, View):
    def post(self, request, pk):
        key = get_object_or_404(ApiKey, organization=self.organization, pk=pk)
        services.revoke_key(organization=self.organization, key_id=key.pk)
        audit.record(AuditAction.API_KEY_REVOKED, request=request, organization=self.organization, target=key.name, prefix=key.prefix)
        messages.success(request, _("API key revoked."))
        return redirect("api_keys:list")
