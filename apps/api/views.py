"""REST API v1: JSON over HTTPS, authenticated with an organization API key
(``Authorization: Bearer eas_…``). Reads need the key creator's role to allow viewing, writes
to allow recording transactions. Writes go through the same services as the web pages, so
the same rules (and database guards) apply. Responses and error messages are in English."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.utils import translation
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.throttle import ThrottleScope, allow
from apps.audit.context import set_actor
from apps.contacts.forms import ContactForm
from apps.contacts.models import Contact
from apps.regions.models import Province
from apps.contacts.services import create_contact
from apps.core.exceptions import DomainError
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.core.sanitizers import sanitize_text
from apps.invoicing import services as invoicing
from apps.invoicing.domain import InvoiceStatus
from apps.invoicing.models import Invoice, InvoiceKind, Item, Payment, ReturnNote
from apps.ledger.models import Account, JournalEntry
from apps.ledger.selectors import account_balances
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.models import TaxRate
from apps.tenants.roles import Permission

from .services import authenticate

MAX_PAGE = 100
MAX_OFFSET = 10**9


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def endpoint(*methods: str):
    """Authenticate the key, check the role for the method, throttle, and turn errors into JSON."""

    def decorate(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            with translation.override("en"):
                if request.method not in methods:
                    return _error("Method not allowed.", 405)
                header = request.headers.get("Authorization", "")
                found = authenticate(header[7:].strip()) if header.startswith("Bearer ") else None
                if found is None:
                    return _error("A valid API key is required (Authorization: Bearer <key>).", 401)
                key, membership = found
                needed = Permission.VIEW if request.method == "GET" else Permission.WRITE
                if not membership.has_permission(needed):
                    return _error("The key's role does not allow this.", 403)
                if not allow(ThrottleScope.API, str(key.pk), limit=settings.API_REQUESTS_PER_HOUR, window_seconds=3600):
                    return _error("Too many requests. Try again later.", 429)
                request.organization, request.api_user = key.organization, key.user
                set_actor(key.user, f"{key.user.email} (API key {key.prefix}…)")  # reset by AuditActorMiddleware
                try:
                    return view(request, *args, **kwargs)
                except ApiError as exc:
                    return _error(exc.message, exc.status)
                except DomainError as exc:
                    return _error(str(exc.message), 400)

        return wrapper

    return decorate


# --- Input helpers --------------------------------------------------------------------------


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ApiError("The body must be JSON.") from None
    if not isinstance(data, dict):
        raise ApiError("The body must be a JSON object.")
    return data


def _filter(request, name: str, allowed) -> str:
    """An optional query-string filter, one of ``allowed`` (an enumeration's values)."""
    value = request.GET.get(name, "")
    if value and value not in allowed:
        raise ApiError(f"'{name}' must be one of {', '.join(allowed)}.")
    return value


def _text(data: dict, name: str, *, required: bool = False, max_length: int = 255) -> str:
    value = data.get(name, "")
    if not isinstance(value, str):
        raise ApiError(f"'{name}' must be a string.")
    value = sanitize_text(value).strip()[:max_length]
    if required and not value:
        raise ApiError(f"'{name}' is required.")
    return value


# (max digits, decimal places) as stored: money, quantities, percentages.
MONEY, QUANTITY, PERCENT = (MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES), (14, 3), (5, 2)


def _decimal(data: dict, name: str, *, required: bool = True, default: Decimal | None = None,
             digits: tuple[int, int] = MONEY) -> Decimal:
    if name not in data or data[name] in (None, ""):
        if required:
            raise ApiError(f"'{name}' is required.")
        return default
    value = data[name]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ApiError(f"'{name}' must be a number.")
    try:
        value = Decimal(str(value).strip())
    except InvalidOperation:
        raise ApiError(f"'{name}' must be a number.") from None
    if not value.is_finite():
        raise ApiError(f"'{name}' must be a number.")
    max_digits, places = digits
    if -value.as_tuple().exponent > places or abs(value) >= Decimal(10) ** (max_digits - places):
        raise ApiError(f"'{name}' must have at most {max_digits - places} digits before and {places} after the decimal point.")
    return value


def _date(data, name: str, *, required: bool = True) -> date | None:
    value = data.get(name)
    if not value:
        if required:
            raise ApiError(f"'{name}' is required (YYYY-MM-DD).")
        return None
    try:
        parsed = parse_date(value) if isinstance(value, str) else None
    except ValueError:  # well formed, but no such day (e.g. 2026-02-30)
        parsed = None
    if parsed is None:
        raise ApiError(f"'{name}' must be a date (YYYY-MM-DD).")
    return parsed


def _one(queryset, value, name: str, field: str = "pk"):
    if value in (None, ""):
        return None
    try:
        return queryset.get(**{field: value})
    except (queryset.model.DoesNotExist, ValueError, TypeError):
        raise ApiError(f"'{name}' was not found.", 404) from None


def _page(request, queryset):
    try:
        limit = min(max(int(request.GET.get("limit", 50)), 1), MAX_PAGE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        raise ApiError("'limit' and 'offset' must be whole numbers.") from None
    if offset > MAX_OFFSET:  # beyond any list, and beyond what the database takes
        raise ApiError(f"'offset' can be at most {MAX_OFFSET}.")
    return queryset[offset:offset + limit], {"limit": limit, "offset": offset, "count": queryset.count()}


def _money(value) -> str:
    return f"{value:.2f}"


# --- Representations --------------------------------------------------------------------------


def account_json(a: Account) -> dict:
    return {"code": a.code, "name": a.name, "category": a.category, "system_role": a.system_role or None, "is_active": a.is_active}


def _region_codes(village) -> dict | None:
    """The Kemendagri codes of each level (null for an address without a region)."""
    if village is None:
        return None
    district = village.district
    return {"province": district.regency.province_id, "regency": district.regency_id, "district": district.pk, "village": village.pk}


def contact_json(c: Contact) -> dict:
    return {
        "id": c.pk, "kind": c.kind, "name": c.name, "npwp": c.npwp, "email": c.email, "phone": c.phone,
        "address": c.address, "village": c.village_id, "region": c.region_line, "postal_code": c.postal_code,
        "region_codes": _region_codes(c.village),
        "city": c.regency_name,  # kept for clients of the earlier free-text field
        "payment_terms_days": c.payment_terms_days, "is_active": c.is_active,
    }


def item_json(i: Item) -> dict:
    return {
        "id": i.pk, "code": i.code, "name": i.name, "type": i.type, "unit": i.unit, "sale_price": _money(i.sale_price),
        "purchase_price": _money(i.purchase_price), "tax_rate": i.tax_rate_id, "track_inventory": i.track_inventory,
    }


def invoice_json(i: Invoice, *, lines: bool = False) -> dict:
    data = {
        "id": i.pk, "kind": i.kind, "number": i.number, "status": i.status, "contact": i.contact_id, "contact_name": i.party_name,
        "date": i.date.isoformat(), "due_date": i.due_date.isoformat(), "reference": i.reference,
        "tax_invoice_number": i.tax_invoice_number, "subtotal": _money(i.subtotal), "tax_amount": _money(i.tax_amount),
        "luxury_tax_amount": _money(i.luxury_tax_amount), "total": _money(i.total), "amount_paid": _money(i.amount_paid),
        "amount_credited": _money(i.amount_credited), "balance_due": _money(i.balance_due),
    }
    if lines:
        data["lines"] = [
            {"id": line.pk, "description": line.description, "account": line.account_label, "quantity": f"{line.quantity.normalize():f}",
             "unit_price": _money(line.unit_price), "discount_percent": f"{line.discount_percent.normalize():f}",
             "tax": line.tax_label or None, "amount": _money(line.amount), "item": line.item_id}
            for line in i.lines.all()
        ]
        data["payments"] = [payment_json(p) for p in i.payments.all()]
        data["returns"] = [return_json(r) for r in i.returns.all()]
    return data


def payment_json(p: Payment) -> dict:
    return {
        "id": p.pk, "number": p.number, "invoice": p.invoice_id, "date": p.date.isoformat(), "amount": _money(p.amount),
        "method": p.method, "cash_account": p.cash_account.code, "vat_withheld": _money(p.vat_withheld),
        "income_tax_kind": p.income_tax_kind or None, "income_tax_withheld": _money(p.income_tax_withheld),
        "income_tax_object": p.income_tax_object or None, "status": p.status,
    }


def entry_json(e: JournalEntry) -> dict:
    return {
        "number": e.number, "date": e.date.isoformat(), "status": e.status, "source": e.source, "description": e.description,
        "reference": e.reference,
        "lines": [{"account": line.account_label, "description": line.description, "debit": _money(line.debit), "credit": _money(line.credit)}
                  for line in e.lines.all()],
    }


# --- Endpoints ----------------------------------------------------------------------------------


@endpoint("GET")
def accounts(request):
    return JsonResponse({"results": [account_json(a) for a in Account.objects.for_organization(request.organization).order_by("code")]})


@endpoint("GET", "POST")
def contacts(request):
    organization = request.organization
    if request.method == "POST":
        defaults = {"kind": Contact.Kind.CUSTOMER, "document_language": settings.LANGUAGE_CODE, "payment_terms_days": 30, "is_active": True}
        form = ContactForm(data={**defaults, **_body(request)}, organization=organization)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid contact.", "fields": form.errors.get_json_data()}, status=400)
        return JsonResponse(contact_json(create_contact(organization=organization, **form.cleaned_data)), status=201)
    queryset = Contact.objects.for_organization(organization).select_related("village__district__regency").order_by("name", "id")
    if kind := _filter(request, "kind", Contact.Kind.values):
        queryset = queryset.filter(kind__in=[kind, Contact.Kind.BOTH])
    if province := _filter(request, "province", list(Province.objects.values_list("code", flat=True))):
        queryset = queryset.filter(village__district__regency__province_id=province)
    page, meta = _page(request, queryset)
    return JsonResponse({"results": [contact_json(c) for c in page], **meta})


@endpoint("GET")
def items(request):
    page, meta = _page(request, Item.objects.for_organization(request.organization).order_by("name", "id"))
    return JsonResponse({"results": [item_json(i) for i in page], **meta})


@endpoint("GET")
def tax_rates(request):
    rates = TaxRate.objects.for_organization(request.organization).order_by("name")
    return JsonResponse({"results": [
        {"id": r.pk, "name": r.name, "rate": f"{r.rate.normalize():f}", "dpp": f"{r.dpp_numerator}/{r.dpp_denominator}",
         "is_active": r.is_active} for r in rates
    ]})


def _line(organization, kind: str, data) -> invoicing.InvoiceLineInput:
    if not isinstance(data, dict):
        raise ApiError("Each line must be an object.")
    accounts = Account.objects.for_organization(organization)
    item = _one(Item.objects.for_organization(organization), data.get("item"), "item")
    account = _one(accounts, data.get("account"), "account", "code")
    if account is None and item is not None:
        account = item.income_account if kind == InvoiceKind.SALES else (item.inventory_account if item.track_inventory else item.expense_account)
    if account is None:
        raise ApiError("Each line needs an 'account' code (or an item with a default account).")
    return invoicing.InvoiceLineInput(
        description=_text(data, "description") or (item.name if item else ""), account=account,
        quantity=_decimal(data, "quantity", required=False, default=Decimal("1"), digits=QUANTITY),
        unit_price=_decimal(data, "unit_price", required=item is None, default=item.sale_price if item and kind == InvoiceKind.SALES else (item.purchase_price if item else None)),
        discount_percent=_decimal(data, "discount_percent", required=False, default=Decimal("0"), digits=PERCENT),
        tax_rate=_one(TaxRate.objects.for_organization(organization), data.get("tax_rate"), "tax_rate"),
        item=item,
    )


@endpoint("GET", "POST")
def invoices(request):
    organization = request.organization
    if request.method == "POST":
        data = _body(request)
        kind = data.get("kind", InvoiceKind.SALES)
        if kind not in InvoiceKind.values:
            raise ApiError("'kind' must be 'sales' or 'purchase'.")
        lines = data.get("lines")
        if not isinstance(lines, list) or not lines or len(lines) > 100:
            raise ApiError("'lines' must be a list of 1 to 100 lines.")
        contact = _one(Contact.objects.for_organization(organization), data.get("contact"), "contact")
        if contact is None:
            raise ApiError("'contact' is required.")
        invoice = invoicing.create_invoice(
            organization=organization, kind=kind, contact=contact, date=_date(data, "date"),
            due_date=_date(data, "due_date", required=False), reference=_text(data, "reference", max_length=100),
            notes=_text(data, "notes", max_length=2000), lines=[_line(organization, kind, line) for line in lines],
            created_by=request.api_user,
        )
        if data.get("issue") is True:
            invoice = invoicing.issue_invoice(invoice=invoice, issued_by=request.api_user)
        return JsonResponse(invoice_json(Invoice.objects.get(pk=invoice.pk), lines=True), status=201)
    queryset = Invoice.objects.for_organization(organization).order_by("-date", "-id")
    if kind := _filter(request, "kind", InvoiceKind.values):
        queryset = queryset.filter(kind=kind)
    if status := _filter(request, "status", InvoiceStatus.values):
        queryset = queryset.filter(status=status)
    page, meta = _page(request, queryset.select_related("contact"))
    return JsonResponse({"results": [invoice_json(i) for i in page], **meta})


@endpoint("GET", "POST")
def invoice_detail(request, pk: int):
    invoice = _one(Invoice.objects.for_organization(request.organization).select_related("contact"), pk, "invoice")
    if request.method == "POST":  # {"action": "issue"}
        if _body(request).get("action") != "issue":
            raise ApiError("Use {\"action\": \"issue\"} to issue a draft.")
        invoice = invoicing.issue_invoice(invoice=invoice, issued_by=request.api_user)
        invoice = Invoice.objects.select_related("contact").get(pk=invoice.pk)
    return JsonResponse(invoice_json(invoice, lines=True))


@endpoint("POST")
def invoice_payments(request, pk: int):
    organization = request.organization
    invoice = _one(Invoice.objects.for_organization(organization), pk, "invoice")
    data = _body(request)
    cash = _one(Account.objects.for_organization(organization), data.get("cash_account"), "cash_account", "code")
    if cash is None:
        raise ApiError("'cash_account' (an account code) is required.")
    method = data.get("method", Payment.Method.TRANSFER)
    if method not in Payment.Method.values:
        raise ApiError(f"'method' must be one of {', '.join(Payment.Method.values)}.")
    income_tax = invoicing.NO_INCOME_TAX
    if (withheld := data.get("income_tax")) is not None:
        if not isinstance(withheld, dict):
            raise ApiError("'income_tax' must be an object.")
        income_tax = invoicing.IncomeTaxInput(
            kind=_text(withheld, "kind", required=True, max_length=10),
            amount=_decimal(withheld, "amount", required=False, default=Decimal("0")),
            account=_one(Account.objects.for_organization(organization), withheld.get("account"), "income_tax.account", "code"),
            slip=_text(withheld, "slip", max_length=50),
            base=_decimal(withheld, "base", required=False, default=Decimal("0")),
            rate=_decimal(withheld, "rate", required=False, digits=PERCENT),
            object_code=_text(withheld, "object_code", max_length=9),
        )
    payment = invoicing.record_payment(
        invoice=invoice, date=_date(data, "date"), amount=_decimal(data, "amount"), cash_account=cash, method=method,
        reference=_text(data, "reference", max_length=100), created_by=request.api_user,
        vat_withheld=_decimal(data, "vat_withheld", required=False, default=Decimal("0")), income_tax=income_tax,
    )
    return JsonResponse(payment_json(payment), status=201)


def return_json(r: ReturnNote) -> dict:
    return {
        "id": r.pk, "number": r.number, "invoice": r.invoice_id, "kind": r.kind, "status": r.status, "date": r.date.isoformat(),
        "reason": r.reason, "subtotal": _money(r.subtotal), "tax_amount": _money(r.tax_amount), "total": _money(r.total),
        "credited": _money(r.credited), "refunded": _money(r.refunded),
        "lines": [{"invoice_line": line.invoice_line_id, "quantity": f"{line.quantity.normalize():f}", "amount": _money(line.amount)}
                  for line in r.lines.all()],
    }


@endpoint("POST")
def invoice_returns(request, pk: int):
    """A sales or purchase return of an issued invoice: {"date", "reason", "lines": [{"line", "quantity"}], "cash_account"}."""
    organization = request.organization
    invoice = _one(Invoice.objects.for_organization(organization), pk, "invoice")
    data = _body(request)
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ApiError("'lines' must be a non-empty list of {\"line\": <invoice line id>, \"quantity\"}.")
    invoice_lines = invoice.lines.all()
    inputs = []
    for line in lines:
        if not isinstance(line, dict):
            raise ApiError("Each line must be an object.")
        inputs.append(invoicing.ReturnLineInput(_one(invoice_lines, line.get("line"), "line"), _decimal(line, "quantity", digits=QUANTITY)))
    note = invoicing.create_return(
        invoice=invoice, date=_date(data, "date"), reason=_text(data, "reason", required=True), lines=inputs,
        cash_account=_one(Account.objects.for_organization(organization), data.get("cash_account"), "cash_account", "code"),
        created_by=request.api_user,
    )
    return JsonResponse(return_json(note), status=201)


@endpoint("GET", "POST")
def journal_entries(request):
    organization = request.organization
    if request.method == "POST":  # {"date", "description", "reference", "lines": [{"account", "debit", "credit", "description"}]}
        data = _body(request)
        lines = data.get("lines")
        if not isinstance(lines, list) or not 2 <= len(lines) <= 200:
            raise ApiError("'lines' must be a list of 2 to 200 lines.")
        accounts = Account.objects.for_organization(organization)
        inputs = []
        for line in lines:
            if not isinstance(line, dict):
                raise ApiError("Each line must be an object.")
            account = _one(accounts, line.get("account"), "account", "code")
            if account is None:
                raise ApiError("Each line needs an 'account' code.")
            inputs.append(JournalLineInput(
                account, debit=_decimal(line, "debit", required=False, default=Decimal("0")),
                credit=_decimal(line, "credit", required=False, default=Decimal("0")), description=_text(line, "description"),
            ))
        entry = create_journal_entry(
            organization=organization, date=_date(data, "date"), description=_text(data, "description", required=True),
            reference=_text(data, "reference", max_length=100), lines=inputs, created_by=request.api_user,
        )
        return JsonResponse(entry_json(entry), status=201)
    queryset = JournalEntry.objects.for_organization(request.organization).order_by("-date", "-id").prefetch_related("lines__account")
    if start := _date(request.GET, "from", required=False):
        queryset = queryset.filter(date__gte=start)
    if end := _date(request.GET, "to", required=False):
        queryset = queryset.filter(date__lte=end)
    page, meta = _page(request, queryset)
    return JsonResponse({"results": [entry_json(e) for e in page], **meta})


@endpoint("GET")
def trial_balance(request):
    as_of = _date(request.GET, "as_of", required=False)
    rows = account_balances(request.organization, date_to=as_of)
    return JsonResponse({
        "as_of": as_of.isoformat() if as_of else None,
        "results": [{**account_json(r.account), "debit": _money(r.debit), "credit": _money(r.credit), "balance": _money(r.balance)} for r in rows],
    })
