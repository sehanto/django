# BukuUMKM: Akuntansi SaaS untuk UMKM Indonesia

Multi-tenant accounting SaaS for Indonesian micro, small and medium enterprises,
built with **Django 5.2 LTS** and **PostgreSQL 18**. The interface is in Bahasa
Indonesia by default, with English available per user. Code, comments and docs are in English.

## Features

| Area | What it does |
|---|---|
| **Multi-tenant SaaS** | Sign-up creates a user plus an organization. Users can belong to several organizations and switch between them. Roles are Pemilik, Administrator, Akuntan and Hanya Lihat. Subscription plans (Gratis / Usaha / Pro) limit users and invoices per month. |
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

## Production

Use `DJANGO_SETTINGS_MODULE=config.settings.prod` and set `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` and `DATABASE_URL`. Then:

```bash
.venv/bin/python manage.py collectstatic --noinput
```

```bash
.venv/bin/gunicorn config.wsgi
```

Static files are served by WhiteNoise.

## Possible next steps

- Payment-gateway billing for plans (Midtrans/Xendit) inside `tenants.services.change_plan`
- e-Faktur / Coretax export, PPh 21/23 withholding
- Inventory with perpetual costing, fixed-asset depreciation schedules
- Email invitations for team members, REST API (the service layer is already delivery-agnostic)
