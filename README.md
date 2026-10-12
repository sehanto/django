# BukuUMKM: Akuntansi SaaS untuk UMKM Indonesia

Multi-tenant accounting SaaS for Indonesian micro, small and medium enterprises,
built with **Django 5.2 LTS** and **PostgreSQL 18**. The interface is in Bahasa
Indonesia by default, with English available per user. Code, comments and docs are in English.

## Features

| Area | What it does |
|---|---|
| **Multi-tenant SaaS** | Sign-up creates a user plus an organization. Users can belong to several organizations and switch between them. Roles are Pemilik, Administrator, Akuntan and Hanya Lihat. Subscription plans (Gratis / Usaha / Pro) limit users and invoices per month, paid monthly or yearly through Xendit (see [Subscription billing](#subscription-billing)). |
| **Bagan akun (SAK EMKM)** | Every new organization gets a default chart of accounts that follows the SAK EMKM statement structure, including contra accounts and *system roles* used by automated postings. |
| **Double-entry ledger** | Manual journals (draft → posted), immutable posted entries, reversal instead of deletion, and a period lock date (*tanggal kunci*). |
| **Penjualan & Pembelian** | Sales invoices and purchase bills: draft → issued → partially paid / paid, or void. Issuing posts the journal and voiding reverses it. Numbering is gap-free in the Indonesian style (`INV/2026/10/0001`). Items catalog and printable invoices are included. |
| **Pembayaran** | Receipts and disbursements per invoice through cash/bank accounts (Tunai, Transfer, QRIS, E-Wallet, Giro). A payment can be voided, which reverses its journal. |
| **PPN** | Configurable rates, with defaults for PMK 131/2024: 12% on DPP 11/12 (effectively 11%) and 12% for luxury goods. Tax is computed per rate on the aggregated base and rounded down to whole rupiah. Only a PKP can charge output VAT. For a non-PKP, input VAT on purchases is capitalized into cost. |
| **PPh Final UMKM** | PP 55/2022 at 0.5% of monthly gross turnover. For individual taxpayers the Rp500 juta annual exemption is consumed chronologically. Deposits are recorded with an NTPN and journaled automatically. Warns when turnover passes Rp4,8 miliar. |
| **Laporan** | Laba Rugi, Posisi Keuangan (with prior-year vs current-year earnings), Neraca Saldo, Buku Besar, Umur Piutang/Utang, Rekap PPN. Statements export to CSV. |
| **Dasbor** | Cash and bank balances, AR/AP with amounts overdue, this month's profit, and a 6-month revenue vs expense chart. |
| **Bahasa** | Bahasa Indonesia or English, per user, with an ID/EN switch everywhere. Invoices print in each customer's or supplier's own document language. See [Languages](#languages). |

## Architecture

```
config/                 settings split by environment (base/dev/test/prod), root URLs
apps/
  core/                 shared kernel: base models, money (IDR) helpers, domain exceptions,
                        document numbering, template filters
  accounts/             custom User (email login), sign-up, profile
  tenants/              Organization, Membership, Role/Permission, Plan, current-org middleware,
                        view mixins, onboarding & offboarding services
  ledger/               chart of accounts, journal entries/lines, double-entry rules
  contacts/             customers & suppliers, NPWP (15/16-digit) validation
  taxes/                PPN rates, PPh Final UMKM computation & deposits
  invoicing/            items, sales/purchase invoices, payments
  reports/              financial statements (pure builders over ledger balances)
  dashboard/            KPIs
templates/  static/
```

Each app uses the same layers. Dependencies point inward, toward `domain`.

| Layer | Responsibility | Rules |
|---|---|---|
| `domain.py` | Business rules and calculations (balancing, tax math, invoice totals, payment state, aging) | Pure Python with no ORM. Unit-tested without a database. |
| `models.py` | Persistence and DB-level invariants (unique & check constraints) | No business workflows. |
| `services.py` | **Write** use cases (`issue_invoice`, `record_payment`, `reverse_journal_entry` …) | Transactional and row-locked. Raise `DomainError` subclasses, never HTTP errors. |
| `selectors.py` | **Read** queries | Always tenant-scoped (`Model.objects.for_organization(org)`). |
| `forms.py` / `views.py` | Delivery: parse input, call one service, render | Thin. `DomainError` becomes a form error or flash message. |

Key design decisions:

- **One posting gateway.** Every module records its accounting impact through
  `ledger.services.create_journal_entry`, so the double-entry, tenant-ownership and
  period-lock invariants live in exactly one place.
- **Tenant isolation is explicit.** `CurrentOrganizationMiddleware` resolves
  `request.organization` and `OrganizationMixin` exposes it to views. Every
  query passes the organization in; there is no thread-local magic. Foreign objects
  return 404, which is covered by tests.
- **Immutability.** Posted journals are never edited or deleted. Corrections are
  reversing entries, and `PROTECT` foreign keys guard ledger history. Tenant
  offboarding goes through `tenants.services.delete_organization`, which deletes in
  dependency order.
- **Money** is a `Decimal` with 2 decimal places. Tax amounts are truncated to whole
  rupiah, and display is `Rp 1.234.567,50`.

## Getting started

Requirements: Python 3.12+ and PostgreSQL 18 (developed on Python 3.14 and PostgreSQL 18.6).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
cp .env.example .env   # then set the real password in DATABASE_URL
```

The app's database user must own the database. Since PostgreSQL 15, only the
database owner can create tables in the `public` schema. As a superuser, run once:

```bash
sudo -u postgres psql -c "ALTER DATABASE msme_db OWNER TO msme_user;"
```

Then:

```bash
.venv/bin/python manage.py migrate
```

```bash
.venv/bin/python manage.py seed_demo
```

```bash
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in with `demo@bukuumkm.id` / `demo12345`, or
register a new business at `/account/signup/`. For the Django admin, run
`manage.py createsuperuser`.

## Tests

```bash
.venv/bin/pytest
```

The tests cover:

- pure domain rules
- services, including journal contents for every posting type
- tenant isolation and role permissions
- full HTTP flows (create → issue → pay)
- statement integrity (trial balance and balance sheet always balance)
- hostile and malformed input (control/bidi characters, NUL bytes, bad parameters, oversized submissions, CSV formulas)
- login rate limiting (lockout, email+IP scoping, reset, cool-off, admin login, proxy IP resolution)
- password reset (single use, expiry, throttling, Host-header safety, language, lockout and session handling)
- both interface languages, on every page, and a complete, compiled Indonesian catalog

The test database is created by `msme_user`, which needs `CREATEDB`.

## Languages

The interface is bilingual (Bahasa Indonesia and English) and follows common practice for
signed-in business software.

| Concern | Policy |
|---|---|
| **Web addresses** | English and language-neutral (`/sales/12/`). The language is not part of the URL, so links work for everyone. A `/id/` or `/en/` prefix (Django `i18n_patterns` with `hreflang`) is only worth adding for a future public, search-indexed marketing site. |
| **Choosing the language** | Most explicit signal wins: (1) a signed-in user's saved preference, (2) a choice made while signed out (cookie), (3) the browser's `Accept-Language`, as a first guess only, (4) Indonesian. A choice made on the sign-in page is saved to the account at login. |
| **Switcher** | ID/EN buttons in the sidebar and on the sign-in pages, labelled with each language's own name for screen readers. No flags, since flags stand for countries, not languages. |
| **Formatting** | The currency is always rupiah. Digit grouping, dates and month names follow the reader's language: `Rp 1.234.567,50` / `12 Oktober 2026` in Indonesian, `Rp 1,234,567.50` / `12 October 2026` in English. |
| **Customer-facing documents** | The printable invoice or bill uses the contact's **document language** (default Indonesian), not the language of whoever prints it. On-screen pages always follow the viewer. |
| **Business records** | The chart of accounts, journal descriptions, and tax-rate and plan names are stored data, written in Indonesian and never machine-translated. Indonesian tax law (UU KUP Art. 28(4)) requires bookkeeping in Bahasa Indonesia unless the Minister of Finance permits a foreign language, so the books stay in Indonesian whatever the interface language. |
| **Caching** | Responses carry `Content-Language` and `Vary: Accept-Language, Cookie`, so shared caches never serve one person's language to another. |
| **Accessibility** | `<html lang>` always matches the rendered language. |

### Working with translations

All interface text is written in English and marked for translation: `gettext` / `gettext_lazy`
in Python, `{% translate %}` / `{% blocktranslate trimmed %}` in templates. Some rules:

- Use named placeholders (`%(name)s`), never f-strings, and translate whole sentences
  rather than gluing fragments together.
- Use plural forms whenever a number is involved: `ngettext` in Python,
  `{% blocktranslate count … %}…{% plural %}…` in templates. Indonesian has a single form;
  English needs "1 user" vs "2 users".
- When a short word means different things in different places, give it a context with
  `pgettext` / `{% translate "…" context "…" %}` instead of rewording it.
- Format amounts in messages with `apps.core.formatting.localized_rupiah`, and with the
  `|rupiah` filter in templates.

The Indonesian text lives in `locale/id/LC_MESSAGES/django.po`. After adding or changing
interface text:

```bash
.venv/bin/python manage.py makemessages -l id --ignore=.venv --no-obsolete
```

Fill in the new `msgstr` entries in `django.po` (and remove any `#, fuzzy` flags), then compile:

```bash
.venv/bin/python manage.py compilemessages -l id --ignore=.venv
```

Commit both `django.po` and `django.mo`, so servers don't need gettext to run the app.
These two commands do need GNU gettext on your machine (`sudo apt install gettext`). The test
suite fails if a message is untranslated, fuzzy, or missing from the compiled `.mo`.

## Input validation and sanitization

Every input is handled in layers, so no single mistake lets bad data through:

| Layer | What it does | Where |
|---|---|---|
| **Sanitize** | All text form inputs are normalized before validation: Unicode NFC, control characters (including NUL, which PostgreSQL rejects) and invisible or bidirectional-override characters removed (so a name can't display differently from what is stored, the "Trojan Source" trick), whitespace tidied. Line breaks are kept only in text areas. Passwords are never altered. | `apps/core/sanitizers.py`, `SanitizedFormMixin` |
| **Validate formats** | Model-field validators, enforced by forms and the Django admin alike: NPWP (15/16 digits), NIB (13 digits), NTPN (16 characters), Faktur Pajak number, phone numbers, account/item codes, non-negative prices, positive payment amounts, payment terms ≤ 365 days, DPP numerator ≤ denominator, length limits on free text. Identifiers typed with dots or spaces (`01.234.567.8-901.000`) are accepted and stored as digits. | `apps/core/validators.py`, model fields |
| **Enforce business rules** | Services re-check what matters to the books, whatever the entry point (forms, admin, commands, a future API): dates must fall between 1 January 2000 and one year ahead, journals must balance, periods must be open, tenant ownership is checked. | `services.py` (`ensure_business_date`, …) |
| **Bound request parameters** | Filters, searches, years and IDs from the URL go through typed helpers: whitelisted choices, sanitized search text capped at 100 characters, bounded integers and years. Malformed values fall back to defaults instead of reaching the database. Invoice and journal forms accept at most 100 lines. | `apps/core/params.py` |
| **Protect outputs** | Django templates auto-escape everything (no `|safe` / `mark_safe` anywhere), and all queries go through the ORM. CSV exports escape cells that a spreadsheet would run as formulas (`=`, `+`, `-`, `@`). Payment links from the gateway must be HTTPS before a customer is redirected to them. | templates, `csv_safe`, billing services |
| **External callbacks** | Webhooks must carry the gateway's token and a JSON object. Only well-formed references are looked up, and the payment status is always re-fetched from the gateway. | `apps/billing` |

`test_every_project_form_sanitizes_input` fails if a new form doesn't inherit `BaseForm` /
`BaseModelForm`.

## Password reset

"Forgot password?" on the sign-in page (and on the lockout page) emails a reset link. The
flow is Django's own: signed, single-use tokens that become invalid once the password changes.
The project adds:

| Behaviour | Why |
|---|---|
| Links expire after **1 hour** (`PASSWORD_RESET_TIMEOUT`; Django's default is 3 days). | A reset link is a credential; keep its exposure short. |
| The "check your email" page looks the same whether or not the address has an account, and inactive accounts get nothing. | No account enumeration. |
| At most **3 emails per address** and **10 requests per IP** per hour; extra requests are silently dropped. | Stops email bombing and address spraying without revealing the limit. |
| The link is built from `SITE_URL`, never from the request's `Host` header. | A forged `Host` can't point the link at an attacker's site. |
| The email is in the **account holder's** language, not the requester's. | The person who receives it can read it. |
| Choosing a new password lifts any login lockout and signs the user out on every other device. | Proving control of the mailbox restores access; stolen sessions die. |

Locally, emails are printed to the `runserver` console.

## Login rate limiting

Sign-in is protected against password guessing with
[django-axes](https://django-axes.readthedocs.io/). This covers the app's login and the Django admin.

| Rule | Setting |
|---|---|
| After **5** failed attempts, sign-in is paused for **15 minutes** (HTTP 429, with a translated page). Every new failed attempt during the pause restarts it. | `AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES` |
| The lock applies to the **email + IP address pair**. Locking by IP alone would block unrelated people who share an address, which is common on Indonesian mobile networks (carrier-grade NAT). Locking by email alone would let anyone lock a user out from anywhere. | `AXES_LOCKOUT_PARAMETERS` |
| Emails are compared case-insensitively, so `Rina@` and `rina@` share one counter (and both can sign in). A successful login clears earlier failures. | `AXES_USERNAME_CALLABLE`, `AXES_RESET_ON_SUCCESS` |

**Behind a reverse proxy, set `TRUSTED_PROXY_COUNT`** to the number of proxies in front of
Django that append to `X-Forwarded-For` (1 for nginx alone, 2 for a CDN plus nginx). The client
IP is then taken as that many entries from the right of the header; anything further left
comes from the client and is ignored, so it can't be spoofed. Leave it at `0` when Django is
reached directly. If it's wrong, every visitor appears to share the proxy's address.

Support tasks:

```bash
.venv/bin/python manage.py axes_list_attempts
```

```bash
.venv/bin/python manage.py axes_reset_username someone@example.com
```

Attempts are also visible, and can be deleted, in the Django admin under **Axes**. Schedule
a daily cleanup of old access logs:

```bash
.venv/bin/python manage.py axes_reset_logs --age 90
```

This limit stops guessing many passwords for one account. It doesn't stop slow
"credential stuffing" spread across many accounts and addresses; for that, add a CAPTCHA or
an IP-reputation service at the proxy.

## Subscription billing

Plans are **prepaid**: each payment buys one month or one year. Customers pay on Xendit's
hosted checkout page with QRIS, bank virtual accounts, e-wallets or cards, so no stored card is needed.

| Rule | Behaviour |
|---|---|
| Prices | Plan prices exclude PPN. PPN is 12% on a tax base (DPP) of 11/12 (effectively 11%) and is added at checkout. Annual = 10 × monthly (2 months free). |
| Renewing | Paying for the current plan extends it from its end date, so no paid days are lost. |
| Switching | A different plan starts on the payment date; remaining time on the old plan is not credited. A plan whose user limit the organization already exceeds can't be bought. |
| Expiry | Owners and admins get an email and an in-app banner 7 days before the end. After the end there are 7 days of grace, then the organization moves to the free plan. Its data is never deleted. |
| Records | Each payment is a `SubscriptionInvoice` with a buyer snapshot and a printable receipt. These are the seller's sales records: they can't be deleted and survive tenant deletion. The official Faktur Pajak is issued separately through Coretax. |

How a payment is confirmed: checkout creates a Xendit **Payment Session** (`POST /sessions`,
`session_type=PAY`, `mode=PAYMENT_LINK`). When Xendit calls the webhook, the app checks the
`x-callback-token` and then re-fetches the session (`GET /sessions/{id}`). The plan is
activated only when that session is `COMPLETED` for the exact invoice amount. Activation is
idempotent, so Xendit's webhook retries are harmless. The customer can also press
**Check payment status**, which runs the same check.

### Setting up Xendit

1. In the Xendit Dashboard, create a **secret API key** with write permission for accepting payments,
   and copy the **webhook verification token** (Settings → Webhooks).
2. Set the payment session webhook URL to `https://<your-domain>/billing/webhooks/xendit/`.
3. Configure the environment (production settings refuse to start without the keys):

   ```
   BILLING_GATEWAY=xendit
   XENDIT_SECRET_KEY=xnd_...
   XENDIT_CALLBACK_TOKEN=...
   SITE_URL=https://<your-domain>
   BILLING_SELLER_NAME=...        # printed on receipts
   BILLING_SELLER_NPWP=...
   BILLING_SELLER_ADDRESS=...
   ```

4. Schedule the daily job (expires stale payment links, sends reminders, downgrades lapsed plans):

   ```bash
   .venv/bin/python manage.py process_billing
   ```

Use Xendit's test-mode keys first. Webhooks can't reach `localhost`, so for local testing
with real keys either expose the dev server through a tunnel, or rely on the
**Check payment status** button, which polls Xendit directly. Xendit only accepts HTTPS return
URLs, so on plain-HTTP dev servers the customer returns from Xendit manually.

Without keys, development and tests use a built-in **fake gateway** (`BILLING_GATEWAY=fake`, the
default outside production). It shows a test payment page with "Simulate successful payment"
and "Simulate expired link" buttons, and the rest of the flow behaves exactly as with Xendit.

If a customer pays by plain bank transfer, staff can confirm it in the Django admin
(Subscription invoices → "Mark as paid (bank transfer confirmed)").

## Production

Use `DJANGO_SETTINGS_MODULE=config.settings.prod` and set `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `SITE_URL` (the public
address used in emails), `EMAIL_URL` (SMTP, e.g. `smtp+tls://user:password@smtp.example.com:587`)
and `DEFAULT_FROM_EMAIL`, plus the billing and proxy settings described above.

Throttling needs a cache shared by all workers. The default in production is Django's
database cache (set `CACHE_URL` to a Redis URL to use Redis instead); create its table once:

```bash
.venv/bin/python manage.py createcachetable
```

Then:

```bash
.venv/bin/python manage.py collectstatic --noinput
```

```bash
.venv/bin/gunicorn config.wsgi
```

Static files are served by WhiteNoise.

## Possible next steps

- Credit unused time when switching plans mid-period (proration)
- Automatic renewal via Xendit `SUBSCRIPTION` sessions for customers who want to save a card or e-wallet
- Issue the Faktur Pajak for subscriptions through the Coretax API
- e-Faktur / Coretax export, PPh 21/23 withholding
- Inventory with perpetual costing, fixed-asset depreciation schedules
- Email invitations for team members, REST API (the service layer is already delivery-agnostic)
