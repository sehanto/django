# EA-System: Akuntansi SaaS untuk UMKM Indonesia

Multi-tenant accounting SaaS for Indonesian micro, small and medium enterprises,
built with **Django 5.2 LTS** and **PostgreSQL 18**. The interface is in Bahasa
Indonesia by default, with English available per user. Code, comments and docs are in English.

## Features

| Area | What it does |
|---|---|
| **Multi-tenant SaaS** | Sign-up creates a user plus an organization. Users can belong to several organizations and switch between them. Roles are Pemilik, Administrator, Akuntan and Hanya Lihat. Owners and admins add registered users directly and invite anyone else by email: a one-time link (valid 7 days, stored only as a keyed hash) adds them after they sign up with that address; pending invitations can be revoked. Subscription plans (Gratis / Usaha / Pro) limit users and invoices per month, paid monthly or yearly through Xendit (see [Subscription billing](#subscription-billing)). |
| **Bagan akun (SAK EMKM)** | Every new organization gets a default chart of accounts that follows the SAK EMKM statement structure, including contra accounts and *system roles* used by automated postings. |
| **Double-entry ledger** | Manual journals (draft → posted), immutable posted entries, reversal instead of deletion, and a period lock date (*tanggal kunci*). |
| **Penjualan & Pembelian** | Sales invoices and purchase bills: draft → issued → partially paid / paid, or void. Issuing posts the journal and voiding reverses it. Numbering is gap-free in the Indonesian style (`INV/2026/10/0001`). Items catalog and printable invoices are included. |
| **Pembayaran** | Receipts and disbursements per invoice through cash/bank accounts (Tunai, Transfer, QRIS, E-Wallet, Giro). A payment can be voided, which reverses its journal. A receipt can record income tax the customer withheld, with its bukti potong number: PPh 22 (e.g. 1.5% by government treasurers) and PPh 23 (2% on services) are debited to a prepaid tax account (default 1-1500 Pajak Dibayar di Muka); PPh Final 0.5% (customers holding your Surat Keterangan PP 55) is debited to Utang PPh Final. The cash received is the rest. Paying a supplier can withhold PPh 23 (e.g. 2% on services) or PPh 4(2) (e.g. 10% on rent of land or buildings) as base × rate: the supplier gets the rest and the tax goes to Utang PPh Pasal 23 / 4 ayat (2) (2-1210/2-1220). **Taxes → PPh 23 & 4(2)** shows what was withheld per month, records the deposit (with NTPN, which settles the payable) and lists the month's bukti potong (CSV, to create them in Coretax eBupot) with their numbers, recorded once. A month already deposited takes no new withholding until its deposit is cancelled. |
| **PPN** | Configurable rates, with defaults for PMK 131/2024: 12% on DPP 11/12 (effectively 11%) and 12% for luxury goods. Tax is computed per rate on the aggregated base and rounded down to whole rupiah. Only a PKP can charge output VAT. For a non-PKP, input VAT on purchases is capitalized into cost. A rate can be PPN *besaran tertentu* (Art. 9A UU PPN: a fixed share of the price, e.g. certain farm produce) and can carry a PPnBM rate (sales tax on luxury goods, on the same base as the PPN): PPnBM on sales is posted to PPnBM Keluaran (2-1150, a system account), on purchases to the cost (it is never credited), and shown in the PPN summary. |
| **PPh Final UMKM** | PP 55/2022 at 0.5% of monthly gross turnover. For individual taxpayers the Rp500 juta annual exemption is consumed chronologically. Accrual basis: each month's tax is recorded as an expense on the month's last day (Beban PPh Final / Utang PPh Final), and the deposit (with its NTPN) settles the liability; recording a deposit records a missing expense first. PPh Final that customers withheld from the month's receipts counts towards that month: the deposit is the rest, and a month they covered entirely needs none (a receipt can't withhold PPh Final for a month already deposited, nor be voided while that deposit stands). Warns when turnover passes Rp4,8 miliar. |
| **Retur** | Sales returns and purchase returns (nota retur, numbered RJ/RB) of issued invoices: lines are taken back at their issued price, discount and PPN rate, up to what was invoiced. The return first reduces the invoice's balance; the rest is refunded through a cash/bank account. PPN is corrected in the month of the return (PPN summary) and PPh Final turnover is net of returns. Tracked stock comes back at the cost it left at (sales) or leaves at its stock cost (purchases, the difference to cost of goods sold). A return can be voided: its journal entry (with any refund) and its stock are reversed and the invoice owes the credited amount again; a sales return only while its returned stock is untouched. When a VAT collector already withheld and deposited the PPN, a sales return leaves that PPN (up to what the collector still holds) out of the credit and the refund: the collector recovers it from the tax office with the nota retur, so PPN Keluaran doesn't change. A payment whose withheld PPN a return left out can only be voided after that return. A refund also gives back the income tax the customer withheld on what it refunds (in the proportion its receipts withheld; the customer corrects its bukti potong): it comes off the prepaid tax, or back onto the PPh Final payable in the month of the return, instead of being paid out. |
| **Coretax (Faktur Pajak Keluaran)** | Issued sales invoices of a PKP export as a Coretax "Faktur Pajak Keluaran" XML import file, per invoice or per month (PPN summary), in the layout of DJP's *Sample Faktur PK Template v.1.4* (rules from template v.1.6.1): transaction code 04 for DPP nilai lain 11/12 or 01 for the full DPP, one row per taxed line with DPP, DPP nilai lain and PPN. After Coretax issues the faktur, its number is recorded on the invoice (once; `invoicing/0014`). Sales to VAT collectors use code 02 (government treasurer) or 03 (other appointed collectors, e.g. BUMN), set on the contact and frozen on the invoice at issue; their payment records the PPN the buyer withheld (Dr PPN Keluaran instead of cash), and the PPN summary shows it as collected by buyers, not payable. Deliveries under a PPN facility use code 07 (not collected / borne by the government) or 08 (exempt), chosen on the invoice with DJP's keterangan tambahan and cap fasilitas lists (`invoicing/facilities.py`) and an optional supporting document: the lines keep their PPN rate, the PPN is shown and exported but not charged (`vat_not_collected`, a column of the PPN summary). Buyers are identified by NPWP or, without one, by NIK, passport or another document with their country (NPWP 0000000000000000, NITKU 000000). A PKP retailer (pedagang eceran, organization setting) reports sales to customers with neither as a monthly total (digunggung): they stay out of the Coretax file and are summed per month on the PPN summary. An organization registers its branches as places of business (settings, each with its 22-digit NITKU and address); a sales invoice made from one carries that NITKU and address, others come from the head office (NPWP + 000000). A contact's branch NITKU is set on the contact. Codes follow DJP's priority (PER-11/PJ/2025): 07/08, then 02/03, then 06 (a tourist's VAT refund, buyer identified by a foreign passport), 05 (PPN besaran tertentu; the faktur shows the 12% rate and the fixed PPN), 09 (assets not originally for sale, e.g. a fixed asset sold on an invoice), 10 (another rate than 12%), then 04/01. PPnBM goes in STLGRate/STLG, also on nota retur. All of it is frozen on the invoice at issue. |
| **Coretax (nota retur)** | Purchase returns export as a Coretax "Retur Pajak Masukan" XML import file, per return or per month (PPN summary), in the layout of DJP's *Sample Retur Faktur PM Template v.1.1* (converter v1.6): 16-digit NPWPs, every row of the supplier's faktur (rows not returned with quantity 0), DPP nilai lain and PPN per row, unit codes (UM.00xx) mapped from the item unit. Sales returns are filed by the customer in Coretax and approved under Retur Pajak Keluaran, so they aren't exported. |
| **Persediaan** | Perpetual inventory for goods whose stock is tracked (opt-in per item): purchase bills add stock at cost (including PPN a non-PKP can't credit), sales invoices post their cost of goods sold in the same entry by FIFO or weighted average (the organization's policy), and selling more than is in stock is refused. Stock adjustments record opening stock and count differences. Each item has a stock card (kartu stok); stock value reconciles with the inventory account. |
| **Aset tetap** | Fixed asset register (a sub-ledger of the fixed asset accounts): cost, useful life, residual value, straight-line or declining-balance depreciation per the organization's policy, land not depreciated. Depreciation is posted monthly (one entry per month, dated its last day, caught up automatically and nightly by cron); sales and write-offs post the gain or loss. A sale can be invoiced instead of received in cash: the disposal issues a sales invoice for the proceeds (with PPN for a PKP, Art. 16D, Faktur Pajak code 09) that credits them to the asset account, and that invoice can't be voided or returned on its own. A revised useful life, residual value or depreciation method applies prospectively (change in estimate) and is disclosed in the notes. The register reconciles with the ledger. |
| **Laporan** | Laba Rugi, Posisi Keuangan (with prior-year vs current-year earnings), Catatan atas Laporan Keuangan, Neraca Saldo, Buku Besar, Umur Piutang/Utang, Rekap PPN. Statements export to CSV. See "SAK EMKM" below. |
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
  contacts/             customers & suppliers, NPWP (15/16-digit), NITKU and identity documents
  taxes/                PPN rates, PPh Final UMKM computation & deposits
  invoicing/            items, sales/purchase invoices, payments
  inventory/            perpetual inventory: stock movements, FIFO/weighted-average valuation, adjustments
  assets/               fixed asset register, depreciation schedules and postings, disposals
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
- **Immutability, enforced by the database.** Posted journals are never edited or
  deleted. Corrections are reversing entries, and `PROTECT` foreign keys guard ledger
  history. PostgreSQL triggers (migration `ledger/0004_immutable_ledger`) make this
  hold for every writer, not just the services:
  - entries are inserted as drafts;
  - "posted" is reachable only by updating a draft whose lines balance (at least two
    lines, debit = credit > 0, the organization's own accounts, after its lock date);
  - posted entries and their lines reject any `UPDATE`/`DELETE` (only `created_by`
    may be cleared when that user is deleted).

  Deleting an organization is the single exception, and it is an owner-only step
  ([Deleting an organization](#deleting-an-organization)). The function
  `ledger_purge_organization(org_id)` wipes one whole organization's ledger and
  only the schema owner may execute it. The guard's `ledger.purge` switch also
  works only for the owner. A role that owns the tables can disable triggers
  anyway, so the app connects as a data-only role
  (see [Database roles](#database-roles)).
- **Source documents are immutable too** (migrations `invoicing/0004`, `taxes/0006`).
  Invoices can be edited or deleted only as drafts. Once issued, everything except the
  paid amount and status is frozen, and their lines are frozen too:
  - the paid amount must equal the posted payments, and the status must follow from it;
  - voiding requires an unpaid invoice whose journal entry has been reversed, and a void
    invoice is final.

  Payments, PPh Final expenses and PPh Final payments are inserted as recorded and can only be voided, after
  their journal entry is reversed (an expense only once its deposit is cancelled; a deposit must settle
  an expense of the same period and amount, `taxes/0009`). They are never deleted: cancelling a PPh Final payment
  keeps it as a void record. Only the owner's organization purge removes them.

  The fixed asset register (migration `assets/0002`): depreciation postings are append-only
  and must match a posted journal entry on the last day of their month; an asset's figures are
  fixed once depreciation is posted, disposal is one step with its journal entry, and assets
  with postings or a disposal can't be deleted. Only the name and description stay editable.
  Returns (`invoicing/0011`, `0013`) are inserted with their posted journal entry for an issued invoice,
  their lines within what was invoiced, and can only be voided (posted -> void, after their entry is
  reversed); the invoice's `amount_credited` must equal its posted returns, and
  payments plus returns credited decide its status. An invoice with posted returns can't be voided.
  `invoicing/0016`: only receipts on sales invoices record income tax withheld, and a sales return
  leaves out at most the PPN the buyer withheld on its payments and still holds. `invoicing/0017`:
  the same for PPN and PPnBM together, and a refund gives back at most the income tax still withheld;
  issuing also snapshots each line's PPnBM rate and besaran tertentu flag. A fixed asset's sales
  invoice is set with its disposal only (`assets/0006`).
  Stock movements (`inventory/0002`) are append-only and need a posted journal entry; once an item
  has movements, its type, stock tracking and accounts are fixed. A voided sale returns its stock at
  the cost it left at; a purchase can be voided only while its own stock is untouched.
  Changes in estimate (`assets/0004`) are append-only and start after every posted month and
  any earlier revision; a mistake is corrected by another revision.

  Reference data stays editable for future documents but can't rewrite history
  (migrations `invoicing/0005`, `taxes/0007`, `ledger/0008`):
  - an account with posted entries keeps its category, contra flag and system role
    (they decide where history lands in the reports and which way it counts); its code,
    name and description can change (recorded in the audit log). Posting copies each
    line's account code and name onto the line (migration `ledger/0009`, written by the
    database), so a rename relabels the chart and the period reports, but past journal
    entries and the journal export keep the name used when they were posted. Issuing an
    invoice does the same for its lines (migration `invoicing/0006`), and copies each
    line's tax rate name, rate and DPP (`invoicing/0007`) and item code (`invoicing/0008`) too. System accounts are
    never deleted, deactivated or stripped of their role, and no account changes
    organization;
  - issuing an invoice snapshots the buyer's and your own name, NPWP, address and city
    onto it. Issued invoices, prints and the export show those snapshots, so editing a
    contact or the organization never changes an issued invoice, and the database refuses
    to issue without them;
  - a tax rate used on an issued invoice keeps its name, rate and DPP; it can only be
    (de)activated (add a new rate to charge a different one);
  - items used on issued invoices, and contacts or tax rates used on any invoice, can't be
    deleted (deactivate them). Items themselves stay editable: lines copy what they need.

  Subscription invoices (our own sales records, migration `billing/0006`) are guarded
  too:
  - their terms (plan, period, buyer, amounts, credit) never change after creation;
  - status only moves forward (pending → paid, expired, canceled or failed; a closed one
    → paid when support confirms a transfer), and paid is final;
  - payment and coverage are recorded once, when the invoice is paid, and "ended early on"
    is set at most once;
  - they are never deleted, not even with the organization.
- **Tamper-evident audit trail.** Posting records who posted (`posted_by`). The database
  (migration `ledger/0006_hash_chain`) also gives each posted entry a gap-free
  `posting_sequence` per organization and a SHA-256 `entry_hash` over its content, its
  lines and the previous entry's hash. `manage.py verify_ledger` (daily in cron)
  recomputes the chain, so even owner-level edits or deletions that bypass the triggers
  are reported. `--expect-head SEQ:HASH` compares against a head recorded earlier; the
  books export README includes it, which catches a fully rewritten chain.
  Chart-of-accounts changes are in the audit log.
- **Money** is a `Decimal` with 2 decimal places. Tax amounts are truncated to whole
  rupiah, and display is `Rp 1.234.567,50`.

## Getting started

Requirements: Python 3.12+ and PostgreSQL 18 (developed on Python 3.14 and PostgreSQL 18.6).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
cp .env.example .env   # then set the real password in DATABASE_URL
```

The schema owner (`msme_user`) must own the database. Since PostgreSQL 15, only the
database owner can create tables in the `public` schema. As a superuser, run once:

```bash
sudo -u postgres psql -c "ALTER DATABASE msme_db OWNER TO msme_user;"
```

Optionally (always in production), let the app connect as a separate data-only role.
See [Database roles](#database-roles). Then:

```bash
.venv/bin/python manage.py migrate
```

```bash
.venv/bin/python manage.py seed_demo
```

```bash
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in with `demo@ea-system.id` / `demo12345`, or
register a new business at `/account/signup/`. For the Django admin, run
`manage.py createsuperuser`.

### Database roles

The app uses two PostgreSQL roles:

| Role | Used by | Can |
|---|---|---|
| Owner (`msme_user`, `DATABASE_OWNER_URL`) | `migrate`, `createcachetable`, the test runner | Everything on its tables: DDL, `DISABLE TRIGGER`, `TRUNCATE` |
| App (`msme_app`, `DATABASE_URL`) | The web app, `seed_demo`, `process_billing` and other commands | `SELECT`/`INSERT`/`UPDATE`/`DELETE` on rows only |

The app role cannot alter tables, disable triggers, switch the ledger guard off, or
delete an organization's ledger. It has no way to remove posted entries. The ledger's immutability
triggers (see Architecture) therefore hold even if the application is compromised. It
also cannot rewrite `django_migrations`.

Set it up once, as a superuser (the password goes into `DATABASE_URL`):

```bash
sudo -u postgres psql -d msme_db -v app_password='CHANGE_ME' < deploy/postgres/create_app_role.sql
```

Then in `.env`:

```
DATABASE_URL=postgres://msme_app:CHANGE_ME@localhost:5432/msme_db
DATABASE_OWNER_URL=postgres://msme_user:OWNER_PASSWORD@localhost:5432/msme_db
```

and run `manage.py migrate`. `migrate` and `createcachetable` reconnect as the
owner automatically. After every `migrate`, the owner grants the app role access to
all tables (`apps/core/db_roles.py`), so new tables need no manual grants.
Without `DATABASE_OWNER_URL`, one role does everything, which is fine for a quick
local setup. `manage.py check --deploy` warns (`core.W001`) when the app connects as an
owner or superuser.

### Deleting an organization

Neither the web app nor the Django admin can delete an organization. The admin
shows the command to use instead. Deleting one wipes its books, so it is a deliberate
operator step that connects as the schema owner:

```bash
.venv/bin/python manage.py delete_organization <organization id> --export-to books.zip
```

It shows what will be removed and asks you to type the organization's name (`--yes`
skips the prompt, for scripts). Without owner credentials it refuses. Indonesian
taxpayers must keep their books for 10 years (UU KUP Art. 28(11)). The command
therefore requires either `--export-to` (it writes the books before deleting) or an
explicit `--skip-export`, for example when the customer already downloaded them.
Subscription invoices (our own sales records) survive.

### Audit log

Sensitive actions are recorded in `audit.AuditEvent` (`apps/audit`). Each event has
the time, user, organization, IP address, browser and what changed.

| Category | Actions |
|---|---|
| Account security | Sign-in (and failed attempts, including a wrong two-factor code), lockout, password change and reset, email change, 2FA on/off, new backup codes, backup code used, device remembered/forgotten |
| Organization | Created, settings changed (each field old → new, e.g. the lock date), member added/removed, books exported, organization deleted |
| Subscription | Plan changed, payment confirmed manually in the admin |
| System | Encryption keys rotated |

Bookkeeping is audited by the ledger itself: entries are immutable and record their
author. Passwords, codes and keys are never recorded.

- **Who sees it:** owners and admins see their organization's events under **Settings →
  Activity log**. Every user sees their own sign-ins and security changes on the
  Security page. Staff see everything, read-only, in the Django admin.
- **Append-only:** a PostgreSQL trigger (migration `audit/0002`) rejects updates and
  deletes for every role. It only allows clearing the user/organization link when that
  row is deleted (email and organization name are snapshotted, so deleted
  organizations stay traceable). For retention, the table owner may also delete events
  and blank their IP address and browser (migration `audit/0003`).
- **Retention:** `manage.py prune_security_logs` runs daily as the owner:

  | Records | Kept for | Setting |
  |---|---|---|
  | Account-security events and django-axes sign-in records (IP address, browser) | 90 days | `SECURITY_LOG_RETENTION_DAYS` |
  | Organization, subscription and system events (settings such as the lock date, members, exports, deletion, plan changes, manual payments, encryption key rotation) | 10 years | `ORGANIZATION_LOG_RETENTION_DAYS` (3650) |

  Organization events document changes to the books, which taxpayers keep for 10 years
  (UU KUP Art. 28(11)). Sign-in history is personal data with no such need. The kept
  organization events lose their IP address and browser after 90 days as well, but still
  show who did what and when. Which
  categories are kept longer is `KEPT_LONGER` in `apps/audit/domain.py`.

To record a new sensitive action, add an `AuditAction` (with its category) and call
`apps.audit.services.record(action, request=..., organization=..., target=..., **details)`
after the action succeeds.

### SAK EMKM

The books follow SAK EMKM (Standar Akuntansi Keuangan Entitas Mikro, Kecil, dan Menengah):

| Requirement | How it is met |
|---|---|
| Minimum statements | Laporan Posisi Keuangan, Laporan Laba Rugi and Catatan atas Laporan Keuangan (Reports → Notes). |
| Accrual basis | Sales and purchases post when the invoice is issued, payments separately; PPh Final is accrued at each month's end and settled by the deposit. |
| Historical cost, entity concept | Amounts are recorded at transaction value. Owner withdrawals go to *Prive*, a contra-equity account. |
| Statement identification | Business name, date or period and "Amounts in Rupiah" on every statement. |
| Comparative information | The position is compared with the previous year end; the income statement with the same period a year earlier (screen and CSV). |
| Notes | Statement of compliance, basis of preparation, policies for revenue, receivables, inventory, fixed assets, liabilities, income tax and PPN (wording follows the organization's settings), one note per statement line group, and free-text additional information. |
| Inventory | At cost, FIFO or weighted average (organization policy), recorded perpetually for tracked items: cost of goods sold with each sale; other goods are expensed when bought. |
| Fixed assets | Cost less accumulated depreciation from the fixed asset register; land not depreciated; monthly depreciation from the month of acquisition; gain or loss on disposal. |
| Changes in estimates | A revised useful life, residual value or depreciation method applies from a month after the depreciation already posted: the remaining book value less the new residual value is depreciated over the remaining months with the (new) method; declining balance continues at twice the rate of the remaining life. Past depreciation is not restated; the notes list the revisions that took effect in the period. |
| Policy choices | Inventory cost formula (FIFO or weighted average) and depreciation method (straight line or declining balance) are organization settings, stated in the notes. |
| Income tax | Current tax only (PPh Final or regular); no deferred tax. |

Valuation follows the order in which documents are posted, not their dates: a backdated purchase
doesn't change the cost of sales posted before it. Stock that can no longer be sold for its cost is
written down to its net realisable value (**Inventory → stock card → Write down**): the loss goes to
cost of goods sold or an other expense, the quantities stay, and the lower value is spread over the
cost layers, so later issues take their cost from it. Write-ups above cost are refused. A mistaken
return is voided, not edited.

### Exporting the books

Owners and admins can download their organization's complete books from
**Organization settings → Export books**, or from the Reports page. The ZIP contains:

- the general journal (one row per line) and the chart of accounts;
- sales invoices, purchase bills, their lines and payments;
- contacts, items, PPN rates, PPh Final expenses and deposits;
- the business profile, today's trial balance and a README.

CSV files are UTF-8 with `;` separators (like the report downloads). Headers are in
the user's language. Amounts use a dot as the decimal separator and dates are ISO
(`YYYY-MM-DD`). Business data is exported as recorded. The export reads one
consistent snapshot (`REPEATABLE READ`) and is limited to `BOOKS_EXPORTS_PER_HOUR`
(default 10) per organization. Operators can write the same archive with:

```bash
.venv/bin/python manage.py export_books <organization id> books.zip --language id
```

## REST API

`/api/v1/` serves JSON for integrations (an online shop, a POS). Owners and admins create keys under
**Settings → API keys**; a key (`eas_…`) is shown once and stored only as a keyed hash. Send it as
`Authorization: Bearer eas_…`. A key acts with the role of the member who created it: viewers can
only read, and the key stops working when that member leaves or the key is revoked. Requests are
limited to `API_REQUESTS_PER_HOUR` (default 1000) per key; responses and errors are English JSON.

| Endpoint | Methods | |
|---|---|---|
| `accounts/` | GET | chart of accounts |
| `contacts/` | GET, POST | `?kind=customer\|supplier`; POST takes the contact form's fields |
| `items/`, `tax-rates/` | GET | |
| `invoices/` | GET, POST | `?kind=`, `?status=`; POST `{"kind", "contact", "date", "lines": [{"account": "4-1000", "description", "quantity", "unit_price", "tax_rate", "item"}], "issue": true}` |
| `invoices/<id>/` | GET, POST | POST `{"action": "issue"}` issues a draft |
| `invoices/<id>/payments/` | POST | `{"date", "amount", "cash_account": "1-1100", "method"}` |
| `journal-entries/` | GET | `?from=`, `?to=` |
| `reports/trial-balance/` | GET | `?as_of=` |

Lists take `?limit=` (max 100) and `?offset=`. Writes go through the same services as the web
pages, so every business rule and database guard applies.

```bash
curl -H "Authorization: Bearer $EA_SYSTEM_KEY" https://app.example.com/api/v1/invoices/?status=issued
```

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
- email verification (gate, link validity and tampering, resend throttle, address correction)
- two-factor authentication (setup, sign-in, replay, back-off, backup codes, disabling, admin, organization requirement, remembered devices, encryption at rest and key rotation)
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

## Two-factor authentication

Users can protect their account with a second factor from **Profile → Security**:

| Part | Behaviour |
|---|---|
| Authenticator app (TOTP) | Any standard app (Google Authenticator, Microsoft Authenticator, 1Password, …). Setup shows a QR code rendered on our server (the secret is never sent to an online QR service) plus a typable key, and is confirmed with the current password and a first code. Codes can't be reused (replay protection) and small clock drift is tolerated. |
| Backup codes | 10 single-use codes, shown once at setup. They can be regenerated with the password, which invalidates the old set. |
| Sign-in | After the password (still rate-limited by axes) the user is **not** signed in until they enter a code. The pending step expires after 5 minutes and allows 5 wrong codes before the password is needed again. Wrong codes also trigger django-otp's exponential back-off, and the page says how long to wait. |
| Turning off | Needs the password and a current code. |
| Notifications | Turning 2FA on or off and regenerating backup codes email the account holder (in their language). |
| Django admin | Requires a verified second factor (`OTPAdminSite`). **Staff must turn on 2FA in the app before they can use the admin.** |

### How 2FA secrets are stored

The database never holds a usable 2FA secret:

| Secret | Stored as |
|---|---|
| Authenticator key | Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, authenticated); the server decrypts it only to check a code. |
| Backup codes | HMAC-SHA256 with a secret key ("pepper"), one-way. Each code has only 40 random bits, so a plain hash could be brute-forced; without the key it can't. |

The key is `FIELD_ENCRYPTION_KEYS`, a list of Fernet keys with the newest first. It is deliberately
separate from `SECRET_KEY` and **required in production**. Production refuses to start if a key isn't
a valid Fernet key, is trivially weak (one repeated byte), is listed twice, or is the development key
derived from `SECRET_KEY`. Generate one with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Keep this key safe and backed up separately from database backups**, in a secrets manager or
> your host's secret settings. If it is lost, every user's 2FA stops working and they must be
> reset by support. If it leaks together with a database copy, the protection is gone.

To rotate it:

1. Put the new key **first** and keep the old one after it, then deploy.
2. Re-encrypt the authenticator keys:

   ```bash
   .venv/bin/python manage.py rotate_encryption_keys
   ```

3. Keep the old key while backup codes made under it are still in use, since one-way hashes can't be
   re-hashed. Remove it once users have regenerated their codes, or accept that their old codes stop working.

Outside production, a development key is derived from `SECRET_KEY` automatically.

Devices are built on django-otp's device API (`AuthenticatorDevice`, `BackupCodeDevice`), so
throttling, replay protection and session verification are django-otp's own. Devices created
before encryption was added were migrated automatically, and the plaintext rows were deleted.
django-otp's admin pages for its plaintext models are switched off.

### Remember this device

On the code step, users can tick **"Don't ask for a code on this device for 30 days"**
(`TWO_FACTOR_REMEMBER_DAYS`). The password is still required on every sign-in; only the code step is skipped.

- **What's stored:** the browser keeps a random secret in a cookie that is `HttpOnly`, `Secure` in production, `SameSite=Lax`, and sent only to `/account/` pages. The server stores only its SHA-256 hash and a label such as "Chrome · Windows".
- **When it stops working:** after 30 days (counted from when it was remembered, not extended by use), on any password change or reset, or when 2FA is turned off.
- **Managing devices:** they're listed on the Security page with "Forget" / "Forget all", and support can revoke them in the Django admin. Remembering a new device emails the account holder.
- **Organization rule:** a remembered sign-in counts as two-factor verified, so it satisfies an organization's 2FA requirement.
- **Staff:** staff accounts (Django admin access) always enter a code; the option isn't offered and existing cookies are ignored.

### Requiring 2FA for an organization

Owners and admins can tick **Require two-factor authentication** in the organization settings.

- **Who can switch it on:** only someone who already uses 2FA, so the rule can't lock out the person who set it.
- **When it's switched on:** members without 2FA get an email in their language, and the Team members page shows each member's 2FA status.
- **Enforcement:** the check is that the session has *proved* a second factor, not merely that the user owns one.
  - Members without 2FA are taken to the setup page.
  - Members whose session started before they turned 2FA on are asked for a code once.
  - Sign-out, the language switch and switching to another organization keep working.
- **Turning 2FA off:** a member can't, while any of their organizations requires it.

A password reset does not bypass 2FA. If someone loses both their phone and their backup codes,
support can delete their devices in the Django admin (**OTP_TOTP / OTP_STATIC**) after verifying
their identity; they can then sign in with the password and set up 2FA again.

## Email verification

New accounts must confirm their email address before using the app. After sign-up the user
is signed in, but every page except the verification flow, sign-out, the language switch and
the Django admin redirects to **"Confirm your email address"** until they open the emailed link.

| Behaviour | Detail |
|---|---|
| Link | Signed and timestamped (`django.core.signing`), valid for **3 days** (`EMAIL_VERIFICATION_TIMEOUT`). It names the user *and* the address, so it stops working if the address changes. Built from `SITE_URL`, emailed in the user's language. Works whether or not the person is signed in. |
| Resend | "Send the link again", at most 3 emails per hour per account. |
| Typos | "Wrong email address?" lets the user correct the address after entering their current password. The old link stops working and a new one is sent. |
| Password reset | Completing a reset also confirms the address, since it proves control of the mailbox. |
| Existing accounts | Accounts created before this feature were marked as verified by a migration. Superusers made with `createsuperuser` are verified automatically. |

Support can see and set `email verified at` on a user in the Django admin.

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

Attempts are also visible, and can be deleted, in the Django admin under **Axes**. Old access
logs (sign-in records with IP address and browser) are deleted daily together with the
sign-in audit events, after `SECURITY_LOG_RETENTION_DAYS` (default 90), by
`prune_security_logs` (see [Scheduled jobs](#scheduled-jobs)).

This limit stops guessing many passwords for one account. The CAPTCHA below slows
guessing from one address across many accounts. Slow "credential stuffing" spread over
many addresses *and* accounts still needs an IP-reputation service at the proxy.

### Login CAPTCHA

The sign-in, sign-up, password-reset, change-password, "send the confirmation link
again", "wrong email address?", "turn on/off two-factor authentication", "create new
backup codes", "confirm it's you" (2FA for an existing session) and sign-in 2FA code forms use
[Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/).

**Sign-up and password reset** always show it. Turnstile usually passes without a puzzle.
It is checked before any other field (`CaptchaFirstFormMixin`). Without a solved CAPTCHA
the form says nothing else and does nothing:
- bots can't use "This email is already registered" to find out who has an account;
- bots can't send reset emails to strangers;
- bots can't use up the per-email and per-IP reset limits of real users.

**Resending the email confirmation link** and **correcting the address** (on the
confirm-your-email page) always show it too. Nothing is checked or sent, and the hourly
email limit isn't used up, until it is solved. This also means the correction form can't
be used to test whether a password is right.

**Changing the password** (signed in) always shows it as well. The current password is
not checked until the CAPTCHA is solved, so a script riding a stolen session can't use
the form to guess it.

**Turning two-factor authentication on or off**, **creating new backup codes** and **"confirm
it's you"** (proving 2FA for a session that started without it, e.g. when an organization
starts requiring it, or before opening the admin) always show it too. So does the **2FA code
step of signing in**: someone who has the password still can't try codes, or use up the
user's wrong-code attempts, without solving it. The password and any 2FA code are not checked until it is solved, so no code
attempt is counted against the user.

**Sign-in** shows it only after repeated failures, so normal users never see it:

- After `LOGIN_CAPTCHA_AFTER_FAILURES` (2) failed sign-ins from one IP address **or** for
  one email within `LOGIN_CAPTCHA_WINDOW_MINUTES` (60), the form shows the widget.
  Turnstile usually passes without a puzzle.
- While the CAPTCHA is required, the password is not checked until Cloudflare confirms the
  token (`apps/accounts/captcha.py`, server-side `siteverify`). Bots therefore can't keep
  testing passwords. If Cloudflare can't be reached, every protected form fails closed
  with "complete the security check". Each token is bound to its form (Turnstile
  `action` `login`, `signup`, `password_reset`, `resend_verification`, `change_email`,
  `change_password`, `enable_two_factor`, `disable_two_factor`, `backup_codes` or
  `verify_session` or `login_code`), so a token solved on one form is refused on the others.
- A correct sign-in, or a password reset, clears the email's count. The address's count
  runs until the window ends.
- django-axes still locks the email+IP pair after 5 failures, even for someone who solves
  every CAPTCHA.
- The admin has no login form of its own. `/admin/login/` sends everyone to this page,
  or to the 2FA step for a signed-in staff session without it.

Setup: in the Cloudflare dashboard, open **Turnstile → Add widget**, add your domain and
choose the "Managed" mode. Then set `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`;
production refuses to start without them, with blank values, or with Cloudflare's test
keys. Development uses Cloudflare's public test
keys, which always pass and show "for testing only". `manage.py check --deploy` warns
(`core.W002`) if those are still in use. Tests use `CAPTCHA_BACKEND=fake`; post
`apps.accounts.captcha.FAKE_PASSING_TOKEN` as `cf-turnstile-response` to pass it.

## Subscription billing

Plans are **prepaid**: each payment buys one month or one year. Customers pay on Xendit's
hosted checkout page with QRIS, bank virtual accounts, e-wallets or cards, so no stored card is needed.

| Rule | Behaviour |
|---|---|
| Prices | Plan prices exclude PPN. PPN is 12% on a tax base (DPP) of 11/12 (effectively 11%) and is added at checkout. Annual = 10 × monthly (2 months free). |
| Renewing | Paying for the current plan extends it from its end date, so no paid days are lost. |
| Switching | A different plan starts on the payment date, and unused paid time on the current plan is **credited** against it (see below). A plan whose user limit the organization already exceeds can't be bought. |
| Expiry | Owners and admins get an email and an in-app banner 7 days before the end. After the end there are 7 days of grace, then the organization moves to the free plan. Its data is never deleted. |
| Records | Each payment is a `SubscriptionInvoice` with a buyer snapshot and a printable receipt. These are the seller's sales records: they can't be deleted and survive tenant deletion. The official Faktur Pajak is issued separately through Coretax. |

**Proration when switching plans.** The credit is the unused value of the current plan:
amount paid (excluding PPN) × unused days ÷ days covered, summed over every paid period still
ahead (stacked renewals included). Today counts as used.

- **Upgrade** (credit smaller than the new price): the credit is deducted and PPN is charged on the
  net amount, so the customer pays only the difference plus its tax. Example: switching from Usaha to
  Pro with 19 of 31 days left credits Rp60.677, so Pro costs Rp209.038 instead of Rp276.390.
- **Downgrade, or credit covering the whole price:** the switch happens immediately with nothing to
  pay, and the leftover credit becomes extra days on the new plan. The card shows the resulting end
  date. A Rp0 receipt (gateway "credit") records it. No balances are kept and nothing is refunded.
- **Not credited:** renewals of the same plan, plans in their grace period, the free plan, and
  time granted without payment (the grandfathered month).
- **Credit is used once:** a switch marks the old plan's paid periods as ended early
  (`superseded_on`). The credit is fixed when checkout starts. If the plan changed before that checkout
  was paid (two checkouts in two tabs, for example), the payment can't reuse the credit; it buys a
  proportional share of the period instead.
- **Receipts** itemize the credit ("Credit for unused Usaha plan (19 days)") before PPN.

How a payment is confirmed: checkout creates a Xendit **Payment Session** (`POST /sessions`,
`session_type=PAY`, `mode=PAYMENT_LINK`). When Xendit calls the webhook, the app checks the
`x-callback-token` and then re-fetches the session (`GET /sessions/{id}`). The plan is
activated only when that session is `COMPLETED` for the exact invoice amount. Activation is
idempotent, so Xendit's webhook retries are harmless. The customer can also press
**Check payment status**, which runs the same check.

**Automatic renewal (optional).** On the plan page an owner can turn on automatic renewal (monthly
or yearly). The customer saves a card or e-wallet on a Xendit Payment Session in SAVE mode
(`session_type=SAVE`, nothing is charged); once the session completes, its `payment_token_id`
is stored encrypted. The nightly `process_billing` job then charges it
`BILLING_AUTO_RENEW_DAYS_BEFORE` days (default 1) before the paid period ends, with a
merchant-initiated Payment Request (`POST /v3/payment_requests`, `payment_token_id`, header
`api-version: XENDIT_PAYMENT_REQUEST_API_VERSION`). The renewal is a normal subscription invoice,
confirmed the same way (re-fetched, exact amount), and charged once per period. A declined charge
emails the owners and admins with the manual link; after 3 failures automatic renewal turns
itself off. Organizations that renew automatically get no reminder emails. Verify the request
fields against your Xendit account in test mode before going live.

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

4. Schedule the daily job (expires stale payment links, sends reminders, downgrades lapsed
   plans): `manage.py process_billing`, installed by `deploy/cron/install.sh` (see
   [Scheduled jobs](#scheduled-jobs)).

Use Xendit's test-mode keys first. Webhooks can't reach `localhost`, so for local testing
with real keys either expose the dev server through a tunnel, or rely on the
**Check payment status** button, which polls Xendit directly. Xendit only accepts HTTPS return
URLs, so on plain-HTTP dev servers the customer returns from Xendit manually.

Without keys, development and tests use a built-in **fake gateway** (`BILLING_GATEWAY=fake`, the
default outside production). It shows a test payment page with "Simulate successful payment"
and "Simulate expired link" buttons, and the rest of the flow behaves exactly as with Xendit.

If a customer pays by plain bank transfer, staff can confirm it in the Django admin
(Subscription invoices → "Mark as paid (bank transfer confirmed)").

## Scheduled jobs

| Job | When | What |
|---|---|---|
| `process_billing` | Daily 01:15 | Close expired payment links, send renewal reminders, downgrade lapsed plans |
| `post_depreciation` | Daily 02:00 | Post the fixed asset depreciation of months that have ended (each month once, missed months caught up; organizations with a locked month are skipped and reported) |
| `verify_ledger` | Daily 04:00 | Recompute the ledger hash chain; failures are mailed (`MAILTO`) |
| `prune_security_logs` | Daily 03:30 | Delete sign-in/security records after 90 days; blank IP/browser on organization/subscription/system events after 90 days and delete them after 10 years (see [Audit log](#audit-log); needs `DATABASE_OWNER_URL`) |

The jobs are defined in `deploy/cron/crontab`. Install them into the current user's
crontab with:

```bash
deploy/cron/install.sh
```

It is idempotent: it replaces its own marked block and leaves other entries alone.
`deploy/cron/install.sh --remove` takes them out again. Times follow the server's time
zone, so set the server to Asia/Jakarta or adjust the times.

Each job runs through `deploy/cron/run.sh`, which:
- runs from the project directory with `.venv/bin/python`;
- takes `DJANGO_SETTINGS_MODULE` from `.env`. This matters because `manage.py` would
  otherwise fall back to the dev settings under cron's minimal environment;
- skips a run while the previous one still holds its lock (`flock`);
- appends one entry per run to `logs/<command>.log`;
- stays silent on success. On failure it writes the output to stderr, so cron emails
  it once `MAILTO` is set in the crontab and the server can send mail.

Rotate `logs/` with logrotate if you keep the server for years. The logs only grow by a
few lines a day.

## Production

Production always redirects plain HTTP to HTTPS (`DJANGO_SECURE_SSL_REDIRECT` may only be unset or true;
behind a TLS proxy it relies on the proxy sending `X-Forwarded-Proto: https`) and sends HSTS for at least
one year with subdomains (`DJANGO_SECURE_HSTS_SECONDS` may only raise it). HSTS preload is opt-in
(`DJANGO_SECURE_HSTS_PRELOAD=true`): it needs the bare domain to send the header too and is hard to
undo. Misspelled true/false values are refused instead of read as false. Session and CSRF cookies
are `Secure`, `HttpOnly`, `SameSite=Lax` and named with the `__Host-` prefix (only this exact host,
over HTTPS, can set them); sessions last at most 14 days (`DJANGO_SESSION_COOKIE_AGE` may only
shorten that).

Use `DJANGO_SETTINGS_MODULE=config.settings.prod` and set `DJANGO_SECRET_KEY` (a random value of at least
50 characters: production refuses short, repetitive, `django-insecure-` or placeholder keys such as
`change-me`),
`DJANGO_ALLOWED_HOSTS` (the public host names; production refuses missing, `*`, local or reserved
example domains, and `SITE_URL`'s host must be one of them), `DJANGO_CSRF_TRUSTED_ORIGINS` (optional: the
site's own forms work without it; any entry must be a bare `https://` origin of a host in
`DJANGO_ALLOWED_HOSTS`, never another site), `DATABASE_URL` (the data-only app role) and
`DATABASE_OWNER_URL` (see [Database roles](#database-roles); production refuses database passwords that
are missing, shorter than 16 characters, repetitive, placeholders or the user name), `BILLING_SELLER_NAME`, `BILLING_SELLER_NPWP` and
`BILLING_SELLER_ADDRESS` (printed on subscription receipts: production refuses to
start if any is missing or blank, and `check --deploy` warns, `core.W003`, about
placeholder values), `TURNSTILE_SITE_KEY` and
`TURNSTILE_SECRET_KEY` (see [Login CAPTCHA](#login-captcha)), `FIELD_ENCRYPTION_KEYS` (see
[How 2FA secrets are stored](#how-2fa-secrets-are-stored)), `SITE_URL` (the public
address used in emails), `EMAIL_URL` (SMTP, e.g. `smtp+tls://user:password@smtp.example.com:587`)
and `DEFAULT_FROM_EMAIL`, plus the billing and proxy settings described above. Production refuses to start
without a live Xendit secret key (`xnd_production_…`; development keys take no real
payments) and the Xendit callback token. Email must really be delivered: `EMAIL_URL`
has to be SMTP (encrypted with `smtp+tls://` or `smtps://`, unless it is a relay on
localhost), `DEFAULT_FROM_EMAIL` a valid address, and `SITE_URL` the public `https://`
address (links in emails are built from it).

Throttling needs a cache shared by all workers. Production refuses local-memory, dummy and file
caches (they would multiply or disable the sign-in, CAPTCHA and reset limits) and accepts the
database cache, Redis or Memcached; `check --deploy` (`core.W004`) also tests that the cache stores
values. The default in production is Django's database cache (set `CACHE_URL` to a Redis URL to use
Redis instead); create its table once:

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

- PPh 21 on salaries (needs a payroll module)
- Write endpoints for more documents in the REST API (bills' payments with withholding, returns, journals)
