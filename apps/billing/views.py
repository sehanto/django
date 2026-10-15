import json
import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import formats
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, TemplateView

from apps.core.exceptions import DomainError
from apps.core.params import choice_param, int_param, text_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import ManageMixin
from apps.tenants.models import Plan

from . import services
from .domain import BillingPeriod, SubscriptionState
from .gateways import get_gateway
from .gateways.base import GatewayError, GatewayName, PaymentStatus
from .models import AutoRenewal, SubscriptionInvoice
from .selectors import auto_renewal_of, invoices_for, plan_offers, seller_of, state_of

logger = logging.getLogger(__name__)


class OverviewView(ManageMixin, TemplateView):
    template_name = "billing/overview.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            offers=plan_offers(self.organization),
            state=state_of(self.organization),
            invoices=invoices_for(self.organization)[:24],
            grace_days=settings.BILLING_GRACE_DAYS,
            states=SubscriptionState,
            periods=BillingPeriod,
            renewal=auto_renewal_of(self.organization),
            renewal_statuses=AutoRenewal.Status,
            **kwargs,
        )


class CheckoutView(ManageMixin, View):
    def post(self, request):
        plan = get_object_or_404(Plan, pk=int_param(request.POST, "plan", default=0))
        period = choice_param(request.POST, "period", BillingPeriod.values)
        if not period:
            return HttpResponseBadRequest()
        try:
            invoice = services.start_checkout(
                organization=self.organization, plan=plan, period=period, created_by=request.user,
                return_url_for=lambda inv: request.build_absolute_uri(reverse("billing:invoice", args=[inv.pk]) + "?returned=1"),
            )
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("billing:overview")
        if invoice.status == SubscriptionInvoice.Status.PAID:  # covered entirely by credit: switched already
            messages.success(request, _("Switched to the %(plan)s plan, paid with your remaining credit. Active until %(date)s.") % {
                "plan": invoice.plan.name, "date": formats.date_format(invoice.coverage_end, "d F Y"),
            })
            return redirect("billing:invoice", pk=invoice.pk)
        return redirect(invoice.payment_url)


class InvoiceDetailView(ManageMixin, DetailView):
    template_name = "billing/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return invoices_for(self.organization)

    def get_object(self, queryset=None):
        invoice = super().get_object(queryset)
        if text_param(self.request.GET, "returned"):  # back from the payment page
            invoice = _sync_with_feedback(self.request, invoice)
        return invoice

    def get_context_data(self, **kwargs):
        return super().get_context_data(seller=seller_of(self.object), **kwargs)


def _sync_with_feedback(request, invoice):
    """Bring a pending invoice up to date, then tell the customer where it stands.

    The webhook often confirms the payment before the customer is redirected
    back, so the message depends on the final status, not on who confirmed it.
    """
    try:
        invoice = services.sync_payment(invoice)  # no-op unless still pending
    except DomainError as exc:
        flash_domain_error(request, exc)
        return invoice
    if invoice.status == SubscriptionInvoice.Status.PAID:
        messages.success(request, _("Payment received. Your %(plan)s plan is active until %(date)s.") % {
            "plan": invoice.plan.name, "date": formats.date_format(invoice.coverage_end, "d F Y"),
        })
    elif invoice.status == SubscriptionInvoice.Status.PENDING:
        messages.info(request, _("We have not received the payment yet. This page updates once it is confirmed."))
    return invoice


class InvoiceRefreshView(ManageMixin, View):
    def post(self, request, pk):
        invoice = get_object_or_404(invoices_for(self.organization), pk=pk)
        _sync_with_feedback(request, invoice)
        return redirect("billing:invoice", pk=invoice.pk)


@method_decorator(csrf_exempt, name="dispatch")  # called by the gateway; authenticated by its token instead
class WebhookView(View):
    http_method_names = ["post"]

    def post(self, request, gateway):
        if gateway != settings.BILLING_GATEWAY:
            raise Http404
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return HttpResponseBadRequest("invalid JSON")
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("JSON object expected")
        try:
            services.handle_webhook(headers=request.headers, payload=payload)
        except services.WebhookRejected:
            logger.warning("Rejected %s webhook from %s", gateway, request.META.get("REMOTE_ADDR"))
            return JsonResponse({"status": "forbidden"}, status=403)
        except GatewayError:
            return JsonResponse({"status": "retry"}, status=503)  # the gateway will retry later
        except services.PaymentAmountMismatch:
            pass  # logged for manual review; retrying would not help
        return JsonResponse({"status": "ok"})


class FakeCheckoutView(ManageMixin, TemplateView):
    """Development stand-in for the hosted payment page (fake gateway only)."""

    template_name = "billing/fake_checkout.html"
    OUTCOMES = (PaymentStatus.PAID, PaymentStatus.EXPIRED)

    def dispatch(self, request, *args, **kwargs):
        if settings.BILLING_GATEWAY != GatewayName.FAKE:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_invoice(self):
        return get_object_or_404(invoices_for(self.organization), gateway=GatewayName.FAKE, gateway_reference=self.kwargs["reference"])

    def get_context_data(self, **kwargs):
        return super().get_context_data(invoice=self.get_invoice(), paid=PaymentStatus.PAID, expired=PaymentStatus.EXPIRED, **kwargs)

    def post(self, request, reference):
        invoice = self.get_invoice()
        outcome = PaymentStatus(choice_param(request.POST, "outcome", self.OUTCOMES, default=PaymentStatus.EXPIRED))
        get_gateway().simulate(reference, outcome)
        services.handle_webhook(headers={}, payload={"reference": reference})  # what the real webhook would do
        return redirect(reverse("billing:invoice", args=[invoice.pk]) + "?returned=1")


class AutoRenewalStartView(ManageMixin, View):
    """Turn on automatic renewal: the customer saves a payment method on the gateway's page."""

    def post(self, request):
        period = choice_param(request.POST, "period", BillingPeriod.values)
        if not period:
            return HttpResponseBadRequest()
        try:
            renewal = services.start_auto_renewal(
                organization=self.organization, period=period, created_by=request.user,
                return_url=request.build_absolute_uri(reverse("billing:auto_renewal_return")),
            )
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("billing:overview")
        return redirect(renewal.setup_url)


class AutoRenewalReturnView(ManageMixin, View):
    """Back from the gateway's page (or "check again"): confirm the saved payment method."""

    def get(self, request):
        renewal = auto_renewal_of(self.organization)
        if renewal is not None:
            try:
                renewal = services.sync_auto_renewal(renewal)
            except DomainError as exc:
                flash_domain_error(request, exc)
                return redirect("billing:overview")
            if renewal.status == AutoRenewal.Status.ACTIVE:
                messages.success(request, _("Automatic renewal is on. Your plan will be renewed with the saved payment method."))
            elif renewal.status == AutoRenewal.Status.SETUP:
                messages.info(request, _("The payment method hasn't been confirmed yet. Check again in a moment."))
        return redirect("billing:overview")


class AutoRenewalCancelView(ManageMixin, View):
    def post(self, request):
        services.cancel_auto_renewal(organization=self.organization)
        messages.success(request, _("Automatic renewal turned off. Renew manually before your plan ends."))
        return redirect("billing:overview")


class FakeSaveView(ManageMixin, TemplateView):
    """Development stand-in for the gateway's "save payment method" page (fake gateway only)."""

    template_name = "billing/fake_save.html"

    def dispatch(self, request, *args, **kwargs):
        if settings.BILLING_GATEWAY != GatewayName.FAKE:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, reference):
        get_object_or_404(AutoRenewal, organization=self.organization, setup_reference=reference)
        return super().get(request, reference=reference)

    def post(self, request, reference):
        get_object_or_404(AutoRenewal, organization=self.organization, setup_reference=reference)
        get_gateway().save_method(reference)
        services.handle_webhook(headers={}, payload={"reference": reference})  # what the real webhook would do
        return redirect("billing:auto_renewal_return")
