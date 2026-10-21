# EA-System: Akuntansi SaaS untuk UMKM Indonesia

Multi-tenant accounting SaaS for Indonesian micro, small and medium enterprises,
built with **Django 5.2 LTS** and **PostgreSQL 18**. The interface is in Bahasa
Indonesia by default, with English available per user. Code, comments and docs are in English.

## Features

| Area | What it does |
|---|---|
| **Multi-tenant SaaS** | Sign-up creates a user plus an organization. Users can belong to several organizations and switch between them. Roles are Pemilik, Administrator, Akuntan and Hanya Lihat. Owners and admins add registered users directly and invite anyone else by email: a one-time link (valid 7 days, stored only as a keyed hash) adds them after they sign up with that address; pending invitations can be revoked. Subscription plans (Starter / Growth / Pro, after a 7-day Starter trial) set the users, invoices per month and features, paid monthly or yearly through Xendit (see [Subscription billing](#subscription-billing)). |
| **Bagan akun (SAK EMKM)** | Every new organization gets a default chart of accounts that follows the SAK EMKM statement structure, including contra accounts and *system roles* used by automated postings. |
| **Double-entry ledger** | Manual journals (draft → posted), immutable posted entries, reversal instead of deletion, and a period lock date (*tanggal kunci*). |
| **Penjualan & Pembelian** | Sales invoices and purchase bills: draft → issued → partially paid / paid, or void. Issuing posts the journal and voiding reverses it. Numbering is gap-free in the Indonesian style (`INV/2026/10/0001`). Items catalog and printable invoices are included. |
| **Pembayaran** | Receipts and disbursements per invoice through cash/bank accounts (Tunai, Transfer, QRIS, E-Wallet, Giro). A payment can be voided, which reverses its journal. A receipt can record income tax the customer withheld, with its bukti potong number: PPh 22 (e.g. 1.5% by government treasurers) and PPh 23 (2% on services) are debited to a prepaid tax account (default 1-1500 Pajak Dibayar di Muka); PPh Final 0.5% (customers holding your Surat Keterangan PP 55) is debited to Utang PPh Final. The cash received is the rest. Paying a supplier can withhold PPh 23 (e.g. 2% on services) or PPh 4(2) (e.g. 10% on rent of land or buildings) as base × rate, or PPh 21 on a fee to an individual who isn't an employee (bukan pegawai: the Art. 17 rates on 50% of the base, per payment, 20% more when the supplier has neither an NPWP nor a NIK): the supplier gets the rest and the tax goes to Utang PPh Pasal 23 / 4 ayat (2) / 21 (2-1210/2-1220/2-1230). **Taxes → Withheld income tax** shows what was withheld per month, records the deposit (with NTPN, which settles the payable) and lists the month's bukti potong (CSV, to create them in Coretax eBupot) with their numbers, recorded once. A month already deposited takes no new withholding until its deposit is cancelled. |
| **PPN** | Configurable rates, with defaults for PMK 131/2024: 12% on DPP 11/12 (effectively 11%) and 12% for luxury goods. Tax is computed per rate on the aggregated base and rounded down to whole rupiah. Only a PKP can charge output VAT. For a non-PKP, input VAT on purchases is capitalized into cost. A rate can be PPN *besaran tertentu* (Art. 9A UU PPN: a fixed share of the price, e.g. certain farm produce) and can carry a PPnBM rate (sales tax on luxury goods, on the same base as the PPN): PPnBM on sales is posted to PPnBM Keluaran (2-1150, a system account), on purchases to the cost (it is never credited), and shown in the PPN summary. The summary gives each month's SPT Masa PPN due date (with the deposit of the PPN payable: the end of the next month, PMK 81/2024, moved to Monday from a weekend) and flags fakturs still without their Faktur Pajak number, in red once the 15th of the next month (their upload deadline; a late faktur is fined 1% of its DPP) has passed. A business that isn't a PKP is told when the year's turnover passes Rp4,8 miliar and by when it must register as one (the end of the next month, PMK 197/2013). |
| **PPh Final UMKM** | PP 55/2022 at 0.5% of monthly gross turnover. For individual taxpayers the Rp500 juta annual exemption is consumed chronologically. Accrual basis: each month's tax is recorded as an expense on the month's last day (Beban PPh Final / Utang PPh Final), and the deposit (with its NTPN) settles the liability; recording a deposit records a missing expense first. PPh Final that customers withheld from the month's receipts counts towards that month: the deposit is the rest, and a month they covered entirely needs none (a receipt can't withhold PPh Final for a month already deposited, nor be voided while that deposit stands). Warns when turnover passes Rp4,8 miliar, and when the term PP 55/2022 allows ends (Art. 59: 7 tax years for an individual, 4 for a CV, Firma, cooperative or PT Perorangan, 3 for a PT, from the year in the settings, or 2018 if earlier); it warns rather than refuses, as later rules have extended the term. Each month shows its deposit due date (the 15th of the next month, PMK 81/2024; a weekend moves it to Monday, public holidays are not known) and is marked overdue while unpaid after it, as is withheld income tax. |
| **Penggajian (payroll)** | Employees (NIK, PTKP status on 1 January, salary, fixed allowances, BPJS membership) and a monthly payroll run: BPJS Ketenagakerjaan (JKK by risk group, JKM 0.3%, JHT 3.7% + 2%, JP 2% + 1% up to the JP wage cap) and BPJS Kesehatan (4% + 1% up to Rp12 juta), and PPh 21 under PP 58/2023 / PMK 168/2023: the monthly effective rate (TER, categories A/B/C, the official tables) on gross income including the employer-paid JKK, JKM and BPJS Kesehatan premiums, and in the last month of the year (December, or the month an employee leaves) the Art. 17 tax on the year's net income (less biaya jabatan 5% up to Rp500.000/month, the employee's JHT and JP, and PTKP) minus what was withheld (negative = a refund). The run posts on the month's last day (journal source "payroll", JG): salaries and the employer's BPJS as expenses, PPh 21 (2-1230), BPJS (2-1240) and net pay (Utang Gaji) as liabilities; paying salaries and paying BPJS are separate entries, and the PPh 21 is deposited with PPh 23/4(2). Payslips, a PPh 21 CSV per month for Coretax eBupot, and bukti potong numbers (set once). Runs are immutable (payroll 0002): only paid once each, or voided while unpaid and before later runs of the year and the month's deposit. Employees are permanent or non-permanent (pegawai tidak tetap): those paid monthly get the monthly TER every month (no year-end step), those paid by the day the daily rates on the average daily gross (0% up to Rp450.000, 0.5% up to Rp2,5 juta, above that Art. 17 on 50% of each day's gross), with the days worked entered in the run. Foreign employees carry a passport number; a non-resident (under 183 days) bears 20% PPh 26 instead (2-1250 Utang PPh Pasal 26, deposited like PPh 21). Someone who becomes a tax resident after January (`resident since`) or leaves Indonesia for good gets the year-end tax on annualised net income, prorated to the months. Without an NPWP (or the NIK of a citizen) PPh 21 is 20% higher. **Payroll → 1721-A1** builds each permanent employee's annual statement (lines 1–21 of the form, printable, and a CSV for the year) from the year's payslips. An employee's income from a previous employer of the year (their 1721-A1, entered on the employee) counts in the last period and fills lines 14 and 19. When the employer bears the tax, a tax allowance equal to the tax is added (gross-up, solved exactly, line 2). Benefits in kind (natura, the taxable value entered in the run) are taxed but not paid out (line 6). Zakat or compulsory religious donations deducted from pay go to a zakat payable account (Payroll → Settings) and lower the year's net income (line 11). Severance pay (pesangon) bears final PPh 21 under PP 68/2009 (0% to Rp50 juta, 5% to Rp100 juta, 15% to Rp500 juta, 25% above, on the cumulative amount over two calendar years; what is paid from the third calendar year on bears the Art. 17 rates on each year's total, not final, code 21-100-25), deposited with PPh 21 and left off the 1721-A1. Severance paid in instalments after an employee has left goes through later runs: a former employee who is still marked active is listed for severance only. A non-resident with a certificate of residence (DGT form) bears the treaty rate of PPh 26 instead of 20%. Not covered: pension and JHT lump sums and withdrawals from pension funds (the pension fund or BPJS pays them and withholds the tax). |
| **Retur** | Sales returns and purchase returns (nota retur, numbered RJ/RB) of issued invoices: lines are taken back at their issued price, discount and PPN rate, up to what was invoiced. The return first reduces the invoice's balance; the rest is refunded through a cash/bank account. PPN is corrected in the month of the return (PPN summary) and PPh Final turnover is net of returns. Tracked stock comes back at the cost it left at (sales) or leaves at its stock cost (purchases, the difference to cost of goods sold). A return can be voided: its journal entry (with any refund) and its stock are reversed and the invoice owes the credited amount again; a sales return only while its returned stock is untouched. When a VAT collector already withheld and deposited the PPN, a sales return leaves that PPN (up to what the collector still holds) out of the credit and the refund: the collector recovers it from the tax office with the nota retur, so PPN Keluaran doesn't change. A payment whose withheld PPN a return left out can only be voided after that return. A refund also gives back the income tax the customer withheld on what it refunds (in the proportion its receipts withheld; the customer corrects its bukti potong): it comes off the prepaid tax, or back onto the PPh Final payable in the month of the return, instead of being paid out. |
| **Coretax (Faktur Pajak Keluaran)** | Issued sales invoices of a PKP export as a Coretax "Faktur Pajak Keluaran" XML import file, per invoice or per month (PPN summary), in the layout of DJP's *Sample Faktur PK Template v.1.4* (rules from template v.1.6.1): transaction code 04 for DPP nilai lain 11/12 or 01 for the full DPP, one row per taxed line with DPP, DPP nilai lain and PPN. After Coretax issues the faktur, its number is recorded on the invoice (once; `invoicing/0014`). Sales to VAT collectors use code 02 (government treasurer) or 03 (other appointed collectors, e.g. BUMN), set on the contact and frozen on the invoice at issue; their payment records the PPN the buyer withheld (Dr PPN Keluaran instead of cash), and the PPN summary shows it as collected by buyers, not payable. Deliveries under a PPN facility use code 07 (not collected / borne by the government) or 08 (exempt), chosen on the invoice with DJP's keterangan tambahan and cap fasilitas lists (`invoicing/facilities.py`) and an optional supporting document: the lines keep their PPN rate, the PPN is shown and exported but not charged (`vat_not_collected`, a column of the PPN summary). Buyers are identified by NPWP or, without one, by NIK, passport or another document with their country (NPWP 0000000000000000, NITKU 000000). A PKP retailer (pedagang eceran, organization setting) reports sales to customers with neither as a monthly total (digunggung): they stay out of the Coretax file and are summed per month on the PPN summary. An organization registers its branches as places of business (settings, each with its 22-digit NITKU and address); a sales invoice made from one carries that NITKU and address, others come from the head office (NPWP + 000000). A contact's branch NITKU is set on the contact. Codes follow DJP's priority (PER-11/PJ/2025): 07/08, then 02/03, then 06 (a tourist's VAT refund, buyer identified by a foreign passport), 05 (PPN besaran tertentu; the faktur shows the 12% rate and the fixed PPN), 09 (assets not originally for sale, e.g. a fixed asset sold on an invoice), 10 (another rate than 12%), then 04/01. PPnBM goes in STLGRate/STLG, also on nota retur. All of it is frozen on the invoice at issue. |
| **Coretax (nota retur)** | Purchase returns export as a Coretax "Retur Pajak Masukan" XML import file, per return or per month (PPN summary), in the layout of DJP's *Sample Retur Faktur PM Template v.1.1* (converter v1.6): 16-digit NPWPs, every row of the supplier's faktur (rows not returned with quantity 0), DPP nilai lain and PPN per row, unit codes (UM.00xx) mapped from the item unit. Sales returns are filed by the customer in Coretax and approved under Retur Pajak Keluaran, so they aren't exported. |
| **Persediaan** | Perpetual inventory for goods whose stock is tracked (opt-in per item): purchase bills add stock at cost (including PPN a non-PKP can't credit), sales invoices post their cost of goods sold in the same entry by FIFO or weighted average (the organization's policy), and selling more than is in stock is refused. Stock adjustments record opening stock and count differences. Each item has a stock card (kartu stok); stock value reconciles with the inventory account. |
| **Aset tetap** | Fixed asset register (a sub-ledger of the fixed asset accounts): cost, useful life (no residual value), straight-line or declining-balance depreciation per the organization's policy, land not depreciated. Depreciation is posted monthly (one entry per month, dated its last day, caught up automatically and nightly by cron); sales and write-offs post the gain or loss. A sale can be invoiced instead of received in cash: the disposal issues a sales invoice for the proceeds (with PPN for a PKP, Art. 16D, Faktur Pajak code 09) that credits them to the asset account, and that invoice can't be voided or returned on its own. A revised useful life or depreciation method applies prospectively (change in estimate) and is disclosed in the notes. The register reconciles with the ledger. |
| **Dibayar/diterima di muka** | Prepayments register: rent, insurance or subscriptions paid ahead are expensed straight-line over the months they cover (SAK EMKM 14.13), rent or royalties received ahead recognised as revenue the same way (14.7). Registering can move an amount the bill or invoice booked to expense or revenue onto Biaya Dibayar di Muka or Pendapatan Diterima di Muka (a reclassification entry on the document's date); each month is then posted on its last day, nightly by cron. A mistake is voided before any month is recognised. |
| **Laporan** | Laba Rugi, Posisi Keuangan (with prior-year vs current-year earnings), Catatan atas Laporan Keuangan, Neraca Saldo, Buku Besar, Umur Piutang/Utang, Rekap PPN. Statements export to CSV. See "SAK EMKM" below. |
| **Unggah berkas (file uploads)** | Contacts, items and accounts from a spreadsheet; the opening balances of the earlier books (trial balance, open invoices and bills, tracked stock) and their fixed asset register; what Coretax gives back (Faktur Pajak numbers, input VAT fakturs, bukti potong numbers); bank statements. Every file is checked first and applied in full or not at all, never twice. See [File uploads](#file-uploads). |
| **Rekonsiliasi bank** | Bank statement lines (uploaded, kept as the bank reported them) reconciled with the posted entries on the bank account: matched automatically when exactly one entry has the same amount within five days, by hand, or by posting what the books don't have yet (bank charges, interest). The page shows the bank's balance against the books and what explains the difference. |
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
  deferrals/            amounts paid or received in advance, recognised straight-line month by month
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

`apps/core/tests/test_architecture.py` keeps the dependencies pointing inward: a domain module imports
no models, selectors or services and never queries; models import no selectors or services; selectors
import no services (calculations both sides need live in `domain` or `selectors`, and services import
them from there); nothing below the views imports views or forms; views write nothing themselves.

Views look single records up directly (`get_object_or_404(Model.objects.for_organization(org), pk=pk)`);
any query with its own conditions or aggregation is a selector, any write a service (even one
field, such as `accounts.services.set_language`). A service function reads as its steps: checks
(`_check_*`), amounts, then journal lines (`_*_lines`), each its own function. Helpers shared by
apps live in `core` (`core.periods` for tax months, `core.money`, `core.params`); a set of
statuses used in more than one place is a constant on the model (`Invoice.OPEN_STATUSES`,
`Invoice.ISSUED_STATUSES`).

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
  The prepayments register (`deferrals/0002`) is the same: a prepayment is fixed once registered
  (only the description changes), voided only before any month is recognised and after its
  reclassification is reversed, never deleted; recognised months are append-only, inside the term,
  never more than the amount, each with a posted journal entry on the month's last day.
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
  Every other change is in the [change history](#change-history-audit-trail).
- **Signed checkpoints outside the database.** The chains are computed in the database, so
  whoever can rewrite it can also recompute them. With `CHAIN_CHECKPOINT_FILE` set,
  `verify_ledger` and `verify_audit_trail` append every organization's chain head to that
  file after each clean run (one JSON line each, `apps/audit/checkpoints.py`), and first
  check that the entry each organization's last checkpoint names still has its hash: a
  chain rewritten and recomputed since then fails. Each line is signed with an HMAC under
  `FIELD_ENCRYPTION_KEYS` (which the database never holds) over its content and the previous
  line's signature, so a line can't be forged, edited, reordered or removed from the middle
  unnoticed. Keep the file off the database server, best on append-only storage (e.g. a
  `chattr +a` file or an object store with retention), and back it up with the keys. Lines
  stay verifiable while their key is in `FIELD_ENCRYPTION_KEYS`: before removing an old key,
  archive the file and let the next run start a new one.
- **Reopening a locked period** (moving the lock date back or removing it) is for the owner
  only, and records "Locked period reopened" in the audit log; moving it forward is open to
  owners and admins.
- **Each write once.** Services are atomic and row-locked (`select_for_update`), and what may happen
  once is a database rule (one posted payroll run or depreciation entry per month, a Faktur Pajak
  number set once, a payment's journal entry reversed once), so the nightly jobs can run again
  without posting twice. A form sent twice (a double click, a resend after a slow answer) is
  processed once: `apps/core/submissions.py` recognises the same rendered form (its masked CSRF
  token and fields) for 60 seconds and sends the duplicate where the first went, and the page
  ignores a second submit while the first is on its way. The API has the `Idempotency-Key`.
- **Money** is a `Decimal` with 2 decimal places. Tax amounts are truncated to whole
  rupiah, and display is `Rp 1.234.567,50`.

## Getting started

Requirements: Python 3.12+ and PostgreSQL 18 (developed on Python 3.14 and PostgreSQL 18.6);
Node.js 20+ only to rebuild the stylesheet (developed on Node 24).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
npm ci                 # Tailwind CSS, for manage.py build_css (the built CSS is committed)
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
.venv/bin/python manage.py load_regions
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
| App (`msme_app`, `DATABASE_URL`) | The web app, `seed_demo`, `process_billing` and other commands | `SELECT`/`INSERT`/`UPDATE`/`DELETE` on rows only; only `SELECT` on the region list (`load_regions` runs as the owner) |

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

### Addresses

Every Indonesian address has a street line (jalan, nomor, RT/RW) typed in, and a region
picked from the official list: province, kabupaten/kota, kecamatan and kelurahan/desa, in
cascading selects. The kelurahan/desa fixes the postcode (kode pos), e.g. Jatake,
Pagedangan, Kabupaten Tangerang, Banten 15330. The list (`apps/regions`, shared by every
organization) holds the 38 provinces, 514 kabupaten/kota, 7,285 kecamatan and 83,762
kelurahan/desa with their Kemendagri codes (Kepmendagri No. 300.2.2-2138 Tahun 2025) and
postcodes, from [cahyadsn/wilayah](https://github.com/cahyadsn/wilayah) and
[cahyadsn/wilayah_kodepos](https://github.com/cahyadsn/wilayah_kodepos) (MIT;
`apps/regions/data/NOTICE.txt`).

- Organizations, places of business, contacts and employees use it. An address with any
  part filled in must be chosen down to its kelurahan/desa; records saved before this
  had their old city appended to the street line and ask for the region the next time they
  are edited. A contact abroad and a non-resident employee write their address in full,
  without a region.
- Only the kelurahan/desa code is stored (`village`); the levels above it and the postcode
  follow from it. The postcode can also be typed instead: it must be on the list, fills the
  selects when one kelurahan/desa has it (or narrows them when several share it, e.g. 15330:
  Jatake and Malang Nengah), and must match the kelurahan/desa chosen. The API takes
  `village` (and checks `postal_code` the same way) and returns `village`, `region`,
  `postal_code` and `region_codes` (the code of each level).
- Issued invoices keep the region as issued: the region line (`contact_city`/`seller_city`) and
  the kelurahan/desa code (`contact_village`/`seller_village`); subscription invoices keep
  `buyer_village`, and the platform as seller with `seller_village`. Documents issued before
  these codes existed keep only the text.
- The platform's own address (on subscription receipts) is configured the same way: the street
  line in `BILLING_SELLER_ADDRESS` and the Kemendagri code of its kelurahan/desa in
  `BILLING_SELLER_VILLAGE` (e.g. `36.03.22.2008`); `check --deploy` (core.W003) warns when the
  code isn't on the list. Each subscription invoice keeps the seller as it was when created, so
  a later move doesn't rewrite earlier receipts.
- Contacts can be filtered by province (list and API `?province=36`).
- `manage.py load_regions` loads the list after `migrate` and updates it when Kemendagri
  revises the codes: new codes are added, renamed ones updated, and codes no longer listed
  are deactivated (never deleted, since addresses may still use them). To update, replace
  `apps/regions/data/wilayah.csv.gz` (`code,name,postal_code`, gzipped) and run it again.

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
| Organization | Created, settings changed (each field old → new, e.g. the lock date), locked period reopened, member added/removed, books exported, organization deleted |
| Subscription | Plan changed, payment confirmed manually in the admin |
| System | Encryption keys rotated |

Bookkeeping is audited by the ledger itself: entries are immutable and record their
author. Every other record is in the change history below. Passwords, codes and keys are
never recorded.

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

### Change history (audit trail)

The database records every row created, changed or deleted in a tenant table
(`audit.RecordChange`, migration `audit/0010`). This covers the ledger, invoices,
payments, returns, payroll, stock, fixed assets, tax payments, and also everything the
immutable documents don't: contacts, items, accounts, tax rates, employees, settings,
places of business, team members, invitations, API keys and drafts. Each change holds the
table, the record, the operation, and the data: the new row, the old row, or the columns
that changed, old → new. It also holds who made the change, the database role, the
transaction (one business action, e.g. an invoice with its lines and journal entry, shares
one) and the time.

- **Written by the database, not the app.** An `AFTER` trigger on each table
  (`audit_record_change()`, `SECURITY DEFINER`) writes the trail, so only changes that
  passed the guards are recorded, and nothing the app does can skip it. The app role may
  read the trail but not insert, change or delete trail rows (`apps/core/db_roles.py`).
  Secrets (token and key hashes, encrypted tokens) and bookkeeping columns (`updated_at`,
  an API key's last use) are left out.
- **Who:** `apps.audit.context` names the actor for the database: the signed-in user of a
  web request (`AuditActorMiddleware`), the API key's creator for API calls, or
  `command:<name>` for management commands and cron jobs. Changes with no named actor
  still record the database role. The actor is a session setting reset after every
  request, so use session pooling, not transaction pooling (e.g. PgBouncer in
  transaction mode), in front of PostgreSQL.
- **Append-only and chained:** trail rows can't be updated or deleted (a trigger rejects
  it for every role). Each organization's changes carry a gap-free `sequence` and a
  SHA-256 `change_hash` over the change and the previous hash, like posted entries.
  `manage.py verify_audit_trail` (daily in cron) recomputes the chain;
  `--organization ID --expect-head SEQ:HASH` checks against a head noted earlier (shown
  on the page and in the books export README). Only `delete_organization` (the schema
  owner) removes an organization's trail, together with the organization.
- **Where:** owners and admins see it under **Settings → Activity log → Change history**,
  filtered by kind of record. Contacts, items, accounts, employees, fixed assets, invoices
  and journal entries link to their own history. The books export includes it as
  `change_history.csv`.
- **New tables:** add the trigger in a migration with
  `audit/0010_audit_trail.track_sql(table, "organization_id", *left_out_columns)`.
  `apps/audit/tests/test_audit_trail.py` fails for a tenant table that isn't recorded.

Three more tables are guarded with it:

- document number sequences only count up, one at a time, and are never deleted, so a
  number is never reused;
- income from a previous employer (1721-A1) is fixed once the employee's year is settled;
- Coretax submissions keep what was sent and their final result.

To record a new sensitive action, add an `AuditAction` (with its category) and call
`apps.audit.services.record(action, request=..., organization=..., target=..., **details)`
after the action succeeds.

### SAK EMKM

The books follow SAK EMKM (Standar Akuntansi Keuangan Entitas Mikro, Kecil, dan Menengah):

| Requirement | How it is met |
|---|---|
| Minimum statements (3.9) | Laporan Posisi Keuangan, Laporan Laba Rugi and Catatan atas Laporan Keuangan (Reports → Notes). |
| Cross-references (6.4) | Every line group on the two statements links to its numbered note (screen and CSV). |
| Statement items (4.2, 5.2) | Current assets, fixed assets, intangible assets, current and long-term liabilities (incl. short-term bank loans and revenue received in advance), equity (with additional paid-in capital for a PT, 13.8); revenue, cost of sales, operating and other expenses, finance costs (beban keuangan) and income tax expense, each its own section. |
| Accrual basis | Sales and purchases post when the invoice is issued, payments separately; PPh Final is accrued at each month's end and settled by the deposit. |
| Historical cost, entity concept | Amounts are recorded at transaction value. Owner withdrawals go to *Prive*, a contra-equity account. |
| Equity by legal form (Bab 13) | The chart's equity accounts follow the legal form: a sole proprietorship has Modal Pemilik and Prive, a CV or Firma Modal Sekutu and Prive Sekutu, a PT Modal Saham, Tambahan Modal Disetor (13.8) and Dividen (a contra account: a PT pays dividends, it has no prive), a cooperative Simpanan Pokok, Simpanan Wajib and Cadangan (its SHU is retained earnings). The notes state the equity policy in the same terms. Organizations created before (ledger 0020) had unused accounts renamed and the missing ones added; accounts with postings keep their name. Changing the legal form in the settings does the same from the old form's accounts to the new one's: unused ones are renamed or deactivated, a deactivated one comes back when the form changes back, and the missing ones are added. |
| Statement identification | Business name, date or period and "Amounts in Rupiah" on every statement. |
| Comparative information | The position is compared with the previous year end; the income statement with the same period a year earlier (screen and CSV). |
| Notes | Statement of compliance, basis of preparation, policies for revenue, receivables, inventory, fixed assets, liabilities, income tax and PPN (wording follows the organization's settings), one note per statement line group (the equity note also shows how equity moved in the year: opening equity, capital paid in or withdrawn per account, such as prive or dividends, the year's profit and closing equity), and free-text additional information. |
| Inventory | At cost, FIFO or weighted average (organization policy), recorded perpetually for tracked items: cost of goods sold with each sale; other goods are expensed when bought. |
| Fixed assets (Bab 11) | Cost less accumulated depreciation from the fixed asset register; land not depreciated; monthly depreciation from the month the asset was acquired and ready for use (its acquisition date), **without a residual value** (11.14: the database refuses one on assets and revisions, assets 0008); no impairment; gain or loss on disposal to other income/expense. |
| Intangible assets (Bab 12) | Bought separately (software, licences): registered like fixed assets on 1-2500 Aset Takberwujud, amortised over a limited life without a residual value (1-2950, 6-1650 Beban Amortisasi; the monthly entry says "Amortisasi aset takberwujud"). Internally generated items are expensed. |
| Investments in joint ventures (Bab 10) | Measured at cost, never impaired, and presented as their own group of assets (1-3000 Investasi pada Ventura Bersama, category `investment`); distributions are income when received (14.6). |
| Prepayments (14.13, 14.7, 14.4a) | Rent and other expenses paid in advance are expensed straight-line over the months they cover, rent and royalties received in advance recognised as revenue the same way (**Prepayments**, `deferrals` app; 4-2300 Pendapatan Sewa). Until then they are an asset (1-1600 Biaya Dibayar di Muka) or a liability (2-1500 Pendapatan Diterima di Muka). Construction contract revenue and costs are recognised as billed (14.5). |
| Receivables (8.10-8.13) | No allowance for doubtful debts. A receivable that can't be collected is written off from its sales invoice (**Write off**): the remaining balance goes to 6-1800 Beban Piutang Tak Tertagih, the invoice shows "Written off" (`ReceivableWriteOff`, guarded like returns, invoicing 0028) and a mistake is voided by reversal. |
| Prior-period errors (7.16) | A general journal can be marked "Correction of an earlier year's error" (`JournalEntry.restates_year`, fixed once posted and part of the hash). It is posted on its own date (the general ledger and trial balance show it there), but the statements show it in the year it corrects: that year's comparative figures are restated ("restated" on the column), this year's profit is not affected, and the notes list each correction with its effect on that year's profit. |
| Consistent policies (7.5) | The inventory cost formula and the default depreciation method are fixed in the settings once stock has been valued or assets registered with them; a change is a retrospective restatement (7.8-7.9) recorded as a prior-period correction. |
| Changes in estimates | A revised useful life or depreciation method applies from a month after the depreciation already posted: the remaining book value is depreciated over the remaining months with the (new) method; declining balance continues at twice the rate of the remaining life. Past depreciation is not restated; the notes list the revisions that took effect in the period. |
| Policy choices | Inventory cost formula (FIFO or weighted average) and depreciation method (straight line or declining balance) are organization settings, stated in the notes. |
| Income tax (Bab 15) | Current tax only (PPh Final, or regular tax through 8-1100 Beban Pajak Penghasilan / 2-1260 Utang PPh Pasal 25/29); no deferred tax. |
| Other policies | Revenue received in advance is a liability (14.4a, 2-1500); interest and dividends when received (14.6); grants as income when received (14.10, 4-2200); employee benefits undiscounted (14.12); borrowing and loan transaction costs expensed (14.14, 8.8); no provisions or contingent liabilities recognised (13.5); foreign currency at the spot rate on the transaction date (16.3). All are stated in the notes. |

Valuation follows the order in which documents are posted, not their dates: a backdated purchase
doesn't change the cost of sales posted before it. Stock that can no longer be sold for its cost is
written down to its net realisable value (**Inventory → stock card → Write down**): the loss goes to
cost of goods sold or an other expense, the quantities stay, and the lower value is spread over the
cost layers, so later issues take their cost from it. Write-ups above cost are refused. A mistaken
return is voided, not edited.

### Downloads (CSV and Excel)

Every table that downloads comes as CSV or Excel (XLSX), through one helper (`apps/core/tables.py`):
the financial statements, trial balance and general ledger (**Reports**, with the filter as shown),
the sales, purchase, payment, contact and item lists (as filtered, every page: `?download=csv` or
`xlsx`, `core.views.ExportMixin`), the month's withholdings, the payroll run's PPh 21, the
year's 1721-A1 and the upload templates. CSV is UTF-8 with a BOM and `;`-separated, as Excel opens
it in either language; XLSX keeps amounts as numbers (each column with its own decimals: money 2,
quantities 3) and dates as dates (times in local time), with the header row frozen, and is written
row by row (openpyxl's write-only mode). A list download is bounded: at most `MAX_EXPORT_ROWS`
rows (50,000; narrow the filter for more) and `LIST_EXPORTS_PER_HOUR` downloads per organization
(60). Download buttons come from `partials/export_links.html`. Text that a
spreadsheet would run as a formula is neutralised in both (a leading `'` in CSV, a text cell in
XLSX). Contacts and items download in the columns of their upload template, so a list can be
edited in a spreadsheet and uploaded again (see [File uploads](#file-uploads)). Coretax, eBupot and
Dokumen Lain files stay in DJP's XML formats.

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

## Sending fakturs to Coretax

DJP has no public Coretax API: a taxpayer sends fakturs through a licensed tax application provider
(PJAP). Under **Settings → Coretax connection** an organization enters its provider's HTTPS address
and access token (stored encrypted). It can then send an invoice, or a month's sales invoices or
purchase returns (from the PPN summary), straight to Coretax instead of downloading and importing the
XML file. The file sent is the same one offered for download. The Faktur Pajak numbers Coretax issues
are recorded on the invoices once, as if typed in, matched by invoice number (`RefDesc`). Pending
submissions are checked every two hours (`sync_coretax`, cron).

The app speaks a small adapter contract (`apps/invoicing/coretax_api.py`): `POST {url}/submissions`
with `{"document_type", "tin", "file_name", "xml"}` (base64) returning `{"id", "status"}`, and
`GET {url}/submissions/{id}` returning `{"status", "items": [{"reference", "number", "error"}]}`.
Providers' APIs differ, so adapt `HttpConnector` (or put a small bridge in front of your provider).
A simulated connection (`fake`) is available for development.

The platform's own fakturs for paid subscription invoices are built the same way (code 04, a
service with DPP 11/12, seller `BILLING_SELLER_*`):

```bash
.venv/bin/python manage.py coretax_subscriptions 2026 10 --output faktur-langganan-2026-10.xml
```

With `CORETAX_CONNECTOR` (`fake` or `http`, plus `CORETAX_API_URL` and `CORETAX_API_TOKEN`) set,
`--send` sends it instead and records the numbers on the subscription invoices (set once; billing 0008).
Buyers without an NPWP are left out.

## Retail sales in total (SPT Masa PPN digunggung)

A retailer (Organization → retailer) reports sales to customers without an NPWP or identity document as a monthly total instead of fakturs. **Reports → PPN** offers each month's import file in DJP's retail format ("Retail_XML Induk IA5 IA9 dan IB" v1.2): one row per invoice and kind of delivery (taxed: 1.A5; facility 07/08: 1.A9; without PPN: 1.B; goods or services), anonymous buyer (NIK of zeros, as in DJP's sample), whole-rupiah amounts, the invoice id as the numeric serial and its number in Info. Import it into the SPT Masa PPN draft in Coretax. Returns of these sales go into the file of the return's month as negative rows (DJP's template has no return field; the PPN of a return reduces the return's period).

## Imports, exports and other tax documents (Dokumen Lain)

DJP's templates "Create DOk Lain Keluaran/Masukan" and "Retur DOk Lain Masukan" report deliveries and acquisitions on a document other than a Faktur Pajak. **Reports → PPN → Other tax documents** offers each month's files, by the date of the document:

| Case | How it's recorded | File and codes |
|---|---|---|
| Export of goods, intangibles or services | A sales invoice marked as an export (PPN 0%: no PPN on its lines); its PEB number and date, entered with it or once after issue | Dokumen Lain Keluaran: type 02, detail 11/12/13, document 4/5, buyer TIN of zeros |
| Import of goods | **Taxes → Imports (PIB)**: the PIB with the import value, PPN impor (input PPN for a PKP, a cost otherwise), PPnBM and import duty (to a cost account) and PPh 22 impor (prepaid tax), paid in cash; the goods are the foreign supplier's bill without PPN. Counted as input PPN in the PIB's month. | Dokumen Lain Masukan: type 03, detail 14, document 6 |
| Bill equivalent to a Faktur Pajak (electricity, telephone…) | A purchase bill marked so, with its own document number and date (no Faktur Pajak number) | Dokumen Lain Masukan: type 04, detail 01, document 8; its returns in Retur Dokumen Lain Masukan |

Returns of exports and imports have no Dokumen Lain return (DJP's return templates take only domestic codes); corrections go through customs. The PIB and the document numbers are guarded like the Faktur Pajak number (set once; invoicing 0023, taxes 0015).

## eBupot files (bukti potong)

**Taxes → Withheld income tax → (month)** builds the month's Coretax eBupot import files in DJP's format (the converter templates' schemas: BPMP v.3, BP21 v.4, BP26, BPA1, BPPU v.3), downloadable as XML or sent through the Coretax connection, which records the bukti potong numbers that come back:

| File | Contents | Tax object code |
|---|---|---|
| BPMP | Permanent employees, January–November: gross and TER rate | 21-100-01 |
| BPA1 | The 1721-A1 of the last period (December or the month they leave), with a previous employer's A1 number when recorded | 21-100-01 |
| BP21 | Non-permanent workers paid monthly (TER); daily workers, one record per day worked (PMK 168/2023), daily TER or 50% at Art. 17 above Rp2,5 juta; severance (final, or Art. 17 from the third calendar year, 21-100-25); PPh 21 on fees paid to individuals with a bill | 21-100-35, 21-100-24, 21-100-30, 21-401-01, the fee's code |
| BP26 | Non-resident employees, with the treaty rate and certificate number (facility `COD`) when given | 27-100-99 |
| BPPU | PPh 23 and 4(2) withheld from suppliers | the payment's code |
| BPNR | PPh 26 withheld from suppliers abroad (the contact's foreign tax ID; a treaty rate below 20% with its certificate of residence, facility `COD`; at a 0% treaty rate nothing is withheld or posted, but the payment is still reported) | 27-1xx, with DJP's deemed % |
| BPSP | Final PPh deposited on your own income (**Taxes → Self-deposited PPh**: rent, construction), posted as an expense of the income's month and its deposit | 28-403-02, 28-409-xx |

BPCY and DDBU are not offered: they are for banks, securities firms, crypto exchanges, marketplaces and government procurement systems withholding in bulk. Supplier withholdings carry their tax object code (kode objek pajak, DJP's list in `apps/taxes/tax_objects.py`); the rate must match the code's, as Coretax checks. Payments recorded before codes existed get theirs once on the month page. Records that already have a bukti potong number are left out, so nothing is sent twice. Daily wages are split evenly over the days (the remainder on the last), and the PPh 21 withheld is the sum of the days' records, so it equals what Coretax computes.

## File uploads

**File uploads** (`apps/uploads`) brings data in from files, CSV (UTF-8 or Windows-1252; comma, semicolon or tab separated, as Excel saves it in either language) or XLSX (the first sheet), up to 2 MB and 5,000 rows. Each kind has a template to download; a file downloaded elsewhere (Coretax, internet banking, another accounting app) needs no editing: the header row is found below any title lines and columns are matched by name in English or Indonesian, in any order. Amounts are read as written (`1.234.567,89`, `1,234,567.89`, `Rp 1.500`, `(1.500)`, `1,500,000.00 CR`), dates day first (`31/01/2026`, `31 Januari 2026`, ISO); anything that can't be read is the row's error, never a guess.

| Upload | What each row does | Rows already there |
|---|---|---|
| Contacts | A customer or supplier, checked by the contact form: NPWP/NITKU format, identity documents, email, phone; the kelurahan/desa by its Kemendagri code, or from the postcode when only one has it, the postcode checked against it | Same NPWP and NITKU, or same name: skipped |
| Items | A good or service, its accounts by code and PPN by the rate's name, checked by the item form (a tracked item needs its inventory and cost of goods sold accounts) | Same code or name: skipped |
| Accounts | A chart of accounts entry by its SAK EMKM category (value or label in either language) | Same code: skipped |
| Opening balances | The trial balance on the last day of the earlier books. Receivables and payables give their customer or supplier and document: each becomes an *opening balance invoice* (`Invoice.opening_balance`), collected, paid or written off like any invoice and in the aging, posted against **3-9000 Ekuitas Saldo Awal** instead of revenue or cost, and left out of the Faktur files, the PPN summary and the plan's invoice count (its tax was reported in the earlier books; it can't be returned). Tracked stock gives the item and quantity (a stock adjustment against 3-9000). The other rows are one journal entry (source *Opening balances*, JS) that takes 3-9000 back to zero. The file must balance, and come before the first transaction recorded here. | — |
| Fixed assets brought over | An asset of the register with the month the earlier books depreciated it through (registering posts nothing; depreciation goes on from the next month). Pro plan, like the register. | — |
| Faktur Pajak numbers (sales) | Coretax's list of output fakturs: the sales invoice named in the faktur's reference (the invoice number this project puts there), or else the one with the buyer's NPWP, the date and the PPN, takes the number (`record_tax_invoice_number`) | Already recorded, another number, a different PPN, a cancelled or rejected faktur: skipped with the reason |
| Input VAT fakturs | Coretax's list of input fakturs: the purchase bill with the supplier's NPWP and the PPN, dated within a month, takes the number. The check lists fakturs without a bill (input VAT not recorded) and the months' bills still without a faktur (input VAT that can't be credited without one, UU PPN Art. 9(9)). | as above |
| Bukti potong numbers | Coretax's list of bukti potong: supplier payments and self deposits by the document number on the bukti potong (the bill's number) and the recipient's NPWP/NIK, payslips by the recipient and the tax period | as above |
| Bank statement | A statement line on the chosen bank account (`banking.StatementLine`), reconciled at once when exactly one posted entry on the account has the same amount within five days (never the opening balances) | The same line in an overlapping download: skipped |

A check runs every row through the same forms and services as the pages inside a transaction that is rolled back, and shows what each row would do. The checked file waits encrypted in the shared cache (an hour, bound to the organization and the user) until it is applied: the rows are run again, all or none, and the file is recorded (`uploads.Upload`, append-only, uploads 0002) with its SHA-256, so the same file is never applied twice, and an audit event *File uploaded into the books* is logged. Uploading needs the write role; viewers see the page and the templates. Coretax result files are reports from elsewhere: a row that disagrees with the books is skipped with the reason rather than refusing the whole file.

Bank statement lines are guarded too (banking 0002): their date, amount, text and balance never change and they are never deleted; reconciling links a line to a posted line on its bank account with the same amount on the same side, which the database checks, and can be undone (the entry stays and is reversed like any other if it was wrong).

## REST API

`/api/v1/` serves JSON for integrations (an online shop, a POS). Owners and admins create keys under
**Settings → API keys**; a key (`eas_…`) is shown once and stored only as a keyed hash. Send it as
`Authorization: Bearer eas_…`. A key acts with the role of the member who created it: viewers can
only read, and the key stops working when that member leaves or the key is revoked. Requests are
limited to `API_REQUESTS_PER_HOUR` (default 1000) per key, and failed authentications to
`API_AUTH_FAILURES_PER_HOUR` (default 30) per IP address; responses and errors are English JSON.

| Endpoint | Methods | |
|---|---|---|
| `accounts/` | GET | chart of accounts |
| `contacts/` | GET, POST | `?kind=customer\|supplier`; POST takes the contact form's fields |
| `items/`, `tax-rates/` | GET | |
| `invoices/` | GET, POST | `?kind=`, `?status=`; POST `{"kind", "contact", "date", "lines": [{"account": "4-1000", "description", "quantity", "unit_price", "tax_rate", "item"}], "issue": true}` |
| `invoices/<id>/` | GET, POST | POST `{"action": "issue"}` issues a draft |
| `invoices/<id>/payments/` | POST | `{"date", "amount", "cash_account": "1-1100", "method", "vat_withheld", "income_tax": {"kind", "amount", "account"} (receipts) or {"kind": "pph23", "base", "rate", "object_code": "24-104-01", "slip"} (bills)}` |
| `invoices/<id>/returns/` | POST | `{"date", "reason", "lines": [{"line": <invoice line id>, "quantity"}], "cash_account"}` |
| `journal-entries/` | GET, POST | `?from=`, `?to=`; POST `{"date", "description", "reference", "lines": [{"account", "debit", "credit", "description"}]}` posts a manual entry |
| `reports/trial-balance/` | GET | `?as_of=` |

Lists take `?limit=` (max 100) and `?offset=`. Writes go through the same services as the web
pages, so every business rule and database guard applies.

Every write is all or nothing: the request runs in one transaction that an error response rolls
back, so a failed request leaves nothing behind (no draft when issuing the invoice fails) and can
simply be sent again. To retry safely after a timeout, send an `Idempotency-Key` header (1-100 of
`A-Z a-z 0-9 _ . : -`, e.g. a UUID) with each POST: the first successful response is kept for 24
hours, and the same request with the same key gets it back (`Idempotent-Replayed: true`) without
recording anything again; the key with a different request is a 422 (`apps/api/idempotency.py`).

```bash
curl -X POST -H "Authorization: Bearer $EA_SYSTEM_KEY" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" -d @payment.json https://app.example.com/api/v1/invoices/42/payments/
```

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

## Front end (Tailwind CSS)

The interface is styled with [Tailwind CSS](https://tailwindcss.com) v4, with
[Lucide](https://lucide.dev) icons (ISC) as inline SVG. Everything is served from this site, not
from a CDN: pages work without third-party requests, and the Content-Security-Policy stays strict.
There is no CSS framework script and no icon font: dialogs are native `<dialog>` elements and the
few behaviours live in `static/js/app.js`.

| File | Built | Sent (Brotli) |
|---|---|---|
| `static/build/app.min.css`: Tailwind (only the utilities in use) + components, one request | 43 KB | 7 KB |
| `templates/icons/<name>.svg`: the 64 Lucide icons in use (of ~2,100), rendered into the page | 30 KB | inline |

- **Source.** `frontend/app.css` is the whole stylesheet: `@import "tailwindcss"`, where class names
  come from (`@source`: templates, scripts and Python code, not tests or migrations), the theme
  (Tailwind's own breakpoints, 640, 768, 1024, 1280 and 1536 px, and font stack), base typography for content
  that Tailwind's reset leaves unstyled, and a small `@layer components`.
- **Utilities first.** Templates use Tailwind utilities for layout, spacing, grid (`grid
  grid-cols-12`, `md:col-span-6`), typography and colour. Only primitives that every page repeats
  are components: buttons (`btn btn-primary`, `btn-outline`, `btn-neutral`, `btn-sm`, `btn-icon` …),
  form controls named like Tailwind's forms plugin (`form-input`, `form-select`, `form-toggle`,
  `form-label`, `form-help`, `form-error`; a field with errors is styled by its `aria-invalid`),
  `card`, `table` (plus `table-sm`, `table-hover`, `table-statement`), `num` for
  amounts, `badge-<colour>` (`primary`, `success`, `warning`, `danger`, `neutral`, `subtle`,
  `inactive`; the colour from `|status_badge`), `alert-<level>` (Django message
  tags), `modal` dialogs and the menu's `nav-link`. Classes built from variables
  (`badge-{{ … }}`, `alert-{{ … }}`) must be components, since Tailwind only generates
  utilities it finds written out.
- **Colours** are semantic tokens: `text-fg`, `text-muted`, `bg-canvas`, `bg-surface`, `bg-subtle`,
  `border-line`/`border-line-strong` (form controls), text colours `text-primary`, `text-danger`,
  `text-success`, `text-warning` (and `-subtle` backgrounds), solid `*-fill` backgrounds for white
  text (`bg-primary-fill`, `bg-danger-fill`, `bg-success-fill`, `bg-neutral-fill`, `bg-inactive-fill`,
  `bg-warning-fill` with dark text), alert text and borders (`text-info-text`, `border-danger-line` …)
  and `bg-series-1`/`-2` for the chart. Each is defined once as `light-dark(light, dark)` in
  `frontend/app.css`, so templates never use `dark:` variants (a test checks), and never an
  arbitrary `[var(--…)]`.
- **Light and dark.** Pages follow the device's setting until the user picks *Light*, *Dark* or
  *Device setting* with the switch under the menu (or on the sign-in pages, or in the profile):
  `accounts.Theme`, saved on the account (`User.theme`) and in a `theme` cookie for signed-out
  pages, rendered as `<html data-theme>` with matching `color-scheme` and `theme-color` metas, so
  the first paint is already right (no script, no flash; the CSP allows none inline). Printing is
  always light. The build adds Lightning CSS's `light-dark()` fallback (`frontend/light-dark.cjs`)
  for browsers from before 2024.
- **Contrast.** Every token pair meets WCAG 2.2 AA in both modes: 4.5:1 for text (on canvas, surface
  and subtle backgrounds, white on every fill, alert text on its background) and 3:1 for control
  borders and the focus ring. `apps/core/tests/test_contrast.py` computes the ratios from the
  tokens and Tailwind's palette, so a token change that breaks one fails the tests.
- **Sizes** come from the theme: Tailwind v4's spacing scale takes any step (`h-45` is 180 px,
  `max-h-130` 520 px), and repeated type sizes are tokens (`text-3xl` 1.875rem for page titles,
  `text-2xl` 1.5rem for section headings and figures). Arbitrary values (`w-[16%]`) are for
  one-off measures only.
- **Checked.** `test_every_class_in_use_is_in_the_build` fails when a template, an `{% icon %}` or a
  widget uses a class the build doesn't have (a typo, a leftover of another framework, a component
  never defined, or a build that wasn't rerun); classes assembled from variables are left to the
  components they name.
- **Icons.** `{% icon "receipt" "me-2 text-primary" %}` (a built-in tag, `apps/core/templatetags/icons.py`)
  renders the icon as inline SVG: 1em square, in the text colour, so text utilities size and colour
  it; decorative (`aria-hidden`), so icon-only buttons need an `aria-label`. `{% icon name %}` also
  takes a variable (the build includes the values templates assign to it, e.g. `icon="landmark"`).
  Names are Lucide's (https://lucide.dev/icons).
- **Build.** `npm ci` installs the pinned Tailwind CLI, Lightning CSS and `lucide-static` (`package.json`, `package-lock.json`;
  development only, `node_modules/` isn't committed and production needs no Node.js). **After
  using a class or icon that isn't in the build yet, run `manage.py build_css`** and commit
  `static/build/` and `templates/icons/`; `test_the_built_assets_are_up_to_date` fails until
  then (it is skipped where the tools aren't installed). The build also copies the icons in use
  into `templates/icons/` (with Lucide's licence) and removes those no longer used. The build is
  reproducible.
  `npm audit` reports `braces` (through the CLI's file watcher, which only `--watch` uses); no
  fixed release exists yet, and the build only passes the project's own glob patterns.
- To upgrade Tailwind or Lucide, change the exact versions in `package.json`, run `npm install`,
  then build and compare the screenshots of the pages.
- WhiteNoise serves the files with content hashes in their names (cached for good) and
  precompressed (`brotli` is in the requirements).

- **Layout.** From the `lg` breakpoint the menu is a fixed sidebar; below it a top bar opens the
  same `<nav>` as a drawer (`data-drawer-open`; Escape, the backdrop or the close button close
  it). The organization switcher is a `<details data-dropdown>`. Light or dark follows the device.
  A "skip to content" link comes first for keyboard users.
- **Type scale.** One scale in `frontend/app.css` (`@theme`), in rem so the browser's font-size setting
  still scales the whole interface. Six steps, each about 1.2× the one below:
  12px (`text-sm`) captions, form help text, field errors, badges, chart labels and menu group
  labels, the smallest; 14px (`text-base`) body text, tables, buttons, form labels, the menu and
  breadcrumbs, the floor for anything read at length; 16px (`text-lg`) form fields on phones, below
  the `md` breakpoint (`text-lg md:text-base`), since iOS zooms into a smaller field; 20px
  (`text-xl`) card and dialog titles (`card-title`, `modal-title`), the brand name, sellers' names
  on invoices and 2FA backup codes; 24px (`text-2xl`) section headings (`section-heading`),
  dashboard figures (by their card's width: 16px in a narrow card, 20px from 8.5rem, 24px from 11rem), plan prices, amounts to pay and the printed invoice heading; 30px (`text-3xl`)
  page titles (`page-title`) and error page headings. Secondary text is always a step below body
  text and every heading level a step above the one below it. Paragraphs, facts on detail pages,
  addresses, notes, alerts, disclosure summaries and every table cell are body text; 12px is only
  for a line that labels or annotates another (a figure's label, a document number under a date, a
  status note, an error). No size is written by hand (`text-[…]`); a test checks.
- **Spacing.** Tailwind's 4px steps, three of them for layout: 8px (`gap-2`) inside a group of
  controls (a filter bar, buttons in a row); 16px (`gap-4`, `mb-4`, `card-body`) between cards and
  form fields and inside cards; 24px (`gap-6`, `mb-6`, `py-6`) between the sections of a page, so
  a grid of cards ends with `mb-6` and a page whose main content and panel share a grid uses
  `gap-6`. The page gutter is 16px on phones and 24px from `lg`.
- **Tables on phones.** List tables and invoice lines carry `table-stack`: below `md` (on screen, never
  in print) each row becomes a block whose cells show their column name (`data-label`) beside the value,
  so amounts are never hidden behind a sideways scroll. A cell without `data-label` is the row's title;
  `stack-corner` puts a status badge or row actions beside it; `stack-hidden` hides a cell on phones
  only (e.g. a totals label the amount already carries). Summary cards pair up on phones
  (`col-span-6`) with `stat-value` figures, as on the dashboard. `|rupiah` keeps "Rp" and its figure
  together with a no-break space.
- **Breadcrumbs.** Every page of the app starts with its trail from the dashboard, e.g. Dashboard ›
  Payroll › Payroll 12/2026 › (employee); a section's own page is Dashboard › Payroll: `{% block breadcrumbs %}{% breadcrumbs %}{% crumb _("Payroll")
  "payroll:index" year=run.year %}{% crumb run_title %}{% endbreadcrumbs %}{% endblock %}`
  (`apps/core/templatetags/breadcrumbs.py`, built in). A crumb takes its label, then a URL name and
  arguments (keywords become the query string); the last is the current page (`aria-current`).
  The tag adds the dashboard crumb; section labels are the sidebar's. The dashboard itself and pages
  outside the app's navigation (errors, sign-in, print views) have none.
- **Content-Security-Policy** (`apps/core/middleware.py`, `CONTENT_SECURITY_POLICY`): scripts,
  styles and fonts only from this site (plus Cloudflare Turnstile for the CAPTCHA), no
  `'unsafe-inline'`, no framing. So templates carry no inline `<script>`, `on…=` handlers or
  `style=` attributes (`test_templates_have_no_inline_scripts_handlers_or_styles`); behaviour is
  declared with data attributes handled by `static/js/app.js`:
  `data-dialog-open="id"` opens a `<dialog>` (`data-dialog-close` closes it; so do Escape and a
  click outside), `data-fill-<field>` on the opener fills that field, `data-confirm="…"` on a form
  asks first in `#confirmModal`, `data-dismiss` closes a message, `data-autosubmit` on a select
  submits its form, `data-print` prints, `data-height` sizes a chart bar. `print:hidden` leaves
  the menu and buttons off paper.
- **Forms** render through `partials/form_fields.html` (labels, `form-input`/`form-select`, a
  checkbox as `form-toggle`, `aria-invalid` on errors, in a 12-column grid: pass `col="col-span-12
  md:col-span-4"` to change the width). Help text and errors carry the ids Django's
  `aria-describedby` points at (`partials/field_feedback.html`); table-row forms name their inputs
  with `aria-label` (`core.forms.GridFormMixin`).
- **Accessibility.** Icons are decorative (`aria-hidden="true"`); icon-only buttons have an
  `aria-label`. Wide tables sit in `overflow-x-auto`, charts have a data table, dialogs are labelled
  by their title.

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
| **Bound lengths** | Every text field of a form is no longer than its column, so an overlong value is a form error, never a database error: email addresses at most 254 characters (Django's form field would take 320), URLs and tokens bounded too; passwords, which are hashed whole, at most 4096 characters, including in Django's own login and password forms. | `apps/core/validators.py`, `SanitizedFormMixin` |
| **Sanitize** | All text form inputs are normalized before validation: Unicode NFC, control characters (including NUL, which PostgreSQL rejects) and invisible or bidirectional-override characters removed (so a name can't display differently from what is stored, the "Trojan Source" trick), whitespace tidied. Line breaks are kept only in text areas. Passwords are never altered. | `apps/core/sanitizers.py`, `SanitizedFormMixin` |
| **Validate formats** | Model-field validators, enforced by forms and the Django admin alike: NPWP (15/16 digits), NIB (13 digits), NTPN (16 characters), Faktur Pajak number, phone numbers, account/item codes, non-negative prices, positive payment amounts, payment terms ≤ 365 days, DPP numerator ≤ denominator, length limits on free text. Numbers of documents from elsewhere (PIB/PEB, bukti potong, BPSP, the 1721-A1 of a previous employer, a facility's supporting document, certificates of residence) take letters, digits, spaces and `. , / ( ) _ : # & -` only, starting with a letter or digit; passport numbers and foreign tax IDs letters, digits, `.`, `/` and `-`. The services that record a number once check it too (`ensure_document_number`), so a number from Coretax or the API meets the same rule. Identifiers typed with dots or spaces (`01.234.567.8-901.000`) are accepted and stored as digits. | `apps/core/validators.py`, model fields |
| **Enforce business rules** | Services re-check what matters to the books, whatever the entry point (forms, admin, commands, a future API): dates must fall between 1 January 2000 and one year ahead, journals must balance, periods must be open, tenant ownership is checked. | `services.py` (`ensure_business_date`, …) |
| **Bound request parameters** | Filters, searches, years and IDs from the URL go through typed helpers: whitelisted choices, sanitized search text capped at 100 characters, bounded integers and years. Malformed values fall back to defaults instead of reaching the database. Tax periods in the path use the `year` and `month` converters: a year outside the books (2000 to next year) or a month outside 1-12 doesn't match, so it is a 404 before any view runs. Invoice and journal forms accept at most 100 lines. | `apps/core/params.py`, `apps/core/converters.py` |
| **Protect outputs** | Django templates auto-escape everything (no `|safe` / `mark_safe` anywhere), and all queries go through the ORM. CSV exports escape cells that a spreadsheet would run as formulas (`=`, `+`, `-`, `@`). Payment links from the gateway must be HTTPS before a customer is redirected to them. | templates, `csv_safe`, billing services |
| **External callbacks** | Webhooks must carry the gateway's token and a JSON object. Only well-formed references are looked up, and the payment status is always re-fetched from the gateway. | `apps/billing` |
| **API input** | The JSON body must be an object; text is sanitized, and refused (never cut) when it is longer than its column; numbers must be numbers (not booleans or lists) that fit the column they go to (money 16+2 digits, quantities 11+3, percentages 3+2); dates must exist (`2026-02-30` is refused); `offset` is at most 10⁹; `kind`/`status` filters must be values of their enumeration. Errors are a 400 with a message, never a 500. | `apps/api/views.py` |
| **Enumerations** | Every fixed set of values (statuses, kinds, tax codes, URL actions such as a payroll run's pay/void) is a `TextChoices`/`StrEnum`, and input is checked against it before use. Unknown values give a 404 (URLs) or a form/API error. Region codes are checked against the Kemendagri list. | `domain.py`, models, views |

`test_every_project_form_sanitizes_input` fails if a new form doesn't inherit `BaseForm` /
`BaseModelForm`, and `test_every_text_field_is_bounded_by_its_column` if a text field has no
limit or a longer one than its column.

### Account and record enumeration

Nothing the app answers tells a stranger, or another tenant, whether an account or a record
exists:

| Where | What the app does |
|---|---|
| Sign-up | The same answer for a new and a registered address: "Check your email". Nobody is signed in at sign-up. A new address gets its confirmation link (then the owner signs in); a registered one gets a notice to its owner instead ("you already have an account: sign in or reset your password"), at most 3 an hour. The password is hashed either way. |
| Password reset | Always "If an account exists for that address, we have sent it a link". |
| Timing | Sign-up and password reset take at least `ACCOUNT_RESPONSE_MIN_SECONDS` (1 s), so how long the work took (creating an account, sending an email) doesn't tell either. |
| Correcting an unconfirmed address | Never "already registered": the new address waits as `pending_email` and replaces the old one only when its link is opened. If another account has it, that account's owner gets the notice and no link is sent. |
| Team invitations | Every address gets an invitation link, registered or not, with the same answer ("Invitation sent"). A registered user joins through the link like anyone else. |
| Sign-in | One message for a wrong email or password; lockouts and the CAPTCHA count failed attempts per address and IP whether or not the account exists, and Django hashes a password for unknown addresses too. |
| API keys | Any unknown, revoked or malformed key gets the same 401. After `API_AUTH_FAILURES_PER_HOUR` (30) failed attempts an IP address gets 429 for the rest of the hour before any key is checked, so a right guess answers like a wrong one. |
| Records | Every lookup is scoped to the current organization: another tenant's invoice, contact or entry is a 404, exactly like a number that doesn't exist (web and API). Tokens in links (invitations, confirmation, reset) are random or signed, never sequential. |

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

New accounts must confirm their email address before using the app. Sign-up ends on a
"Check your email" page without signing in (see [Account and record
enumeration](#account-and-record-enumeration)). A user who signs in before confirming finds
every page except the verification flow, sign-out, the language switch and the Django admin
redirecting to **"Confirm your email address"** until they open the emailed link.

| Behaviour | Detail |
|---|---|
| Link | Signed and timestamped (`django.core.signing`), valid for **3 days** (`EMAIL_VERIFICATION_TIMEOUT`). It names the user *and* the address, so it stops working if the address changes. Built from `SITE_URL`, emailed in the user's language. Works whether or not the person is signed in. |
| Resend | "Send the link again", at most 3 emails per hour per account. |
| Typos | "Wrong email address?" lets the user correct the address after entering their current password. The new address waits as `pending_email` and replaces the old one when its link is opened; the old link stops working. |
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

Three tiers, prepaid monthly or yearly (prices exclude PPN; a year costs 10 months):

| Tier | Per month | Per year | Users | Invoices a month | Includes |
|---|---|---|---|---|---|
| Starter | Rp99.000 | Rp990.000 | 1 | 300 | Bookkeeping: sales and purchases, payments, the ledger, inventory, prepayments, PPN and PPh (Final, withholding), SAK EMKM statements and notes, the books export, and the Coretax/eBupot files to import |
| Growth | Rp249.000 | Rp2.490.000 | 3 | Unlimited | Starter, plus payroll (PPh 21/26, BPJS, 1721-A1) and the REST API |
| Pro | Rp499.000 | Rp4.990.000 | 5 | Unlimited | Growth, plus sending to Coretax through a PJAP and the fixed asset register |

The tiers are `tenants.Plan` rows (seeded by tenants 0015, users per tier from tenants 0016; Usaha became Starter and the earlier Pro
became Growth, at their prices); what each includes is `Plan.features` (`tenants.domain.PlanFeature`).
A feature's pages answer with a page naming the cheapest tier that has it (`PlanFeatureMiddleware`,
the API with a 403); what was recorded with it stays in the books, reports and export, and the
nightly jobs keep posting it (depreciation of registered assets, for one). Tax files stay
downloadable on every tier: only sending them is a feature.

**Trial and read-only.** A new organization tries Starter for `BILLING_TRIAL_DAYS` (7). When the
trial ends, or a paid period and its grace days, the organization is **read-only** until it pays:
everything stays readable and exportable and the plan stays, but nothing new is recorded. Every
write is refused (`ReadOnlyOrganizationMiddleware`; the API answers 402), except paying, the
settings and team, the account and the books export; write pages send members to the plans, and a
banner tells everyone. Nothing is ever downgraded or deleted.

Each payment buys one month or one year. Customers pay on Xendit's
hosted checkout page with QRIS, bank virtual accounts, e-wallets or cards, so no stored card is needed.

| Rule | Behaviour |
|---|---|
| Prices | Plan prices exclude PPN. PPN is 12% on a tax base (DPP) of 11/12 (effectively 11%) and is added at checkout. Annual = 10 × monthly (2 months free). |
| Renewing | Paying for the current plan extends it from its end date, so no paid days are lost. |
| Switching | A different plan starts on the payment date, and unused paid time on the current plan is **credited** against it (see below). A plan whose user limit the organization already exceeds can't be bought. |
| Expiry | Owners and admins get an email and an in-app banner 7 days before the end. After the end there are 7 days of grace, then the organization is read-only until it pays. Its data is never deleted. |
| Records | Each payment is a `SubscriptionInvoice` with a buyer snapshot and a printable receipt. These are the seller's sales records: they can't be deleted and survive tenant deletion. The official Faktur Pajak is issued separately through Coretax. |

**Proration when switching plans.** The credit is the unused value of the current plan:
amount paid (excluding PPN) × unused days ÷ days covered, summed over every paid period still
ahead (stacked renewals included). Today counts as used.

- **Upgrade** (credit smaller than the new price): the credit is deducted and PPN is charged on the
  net amount, so the customer pays only the difference plus its tax. Example: switching from Starter to
  Growth with 19 of 31 days left credits Rp60.677, so Growth costs Rp209.038 instead of Rp276.390.
- **Downgrade, or credit covering the whole price:** the switch happens immediately with nothing to
  pay, and the leftover credit becomes extra days on the new plan. The card shows the resulting end
  date. A Rp0 receipt (gateway "credit") records it. No balances are kept and nothing is refunded.
- **Not credited:** renewals of the same plan, plans in their grace period, the trial, and
  time granted without payment (the grandfathered month).
- **Credit is used once:** a switch marks the old plan's paid periods as ended early
  (`superseded_on`). The credit is fixed when checkout starts. If the plan changed before that checkout
  was paid (two checkouts in two tabs, for example), the payment can't reuse the credit; it buys a
  proportional share of the period instead.
- **Receipts** itemize the credit ("Credit for unused Starter plan (19 days)") before PPN.

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

4. Schedule the daily job (expires stale payment links, sends reminders and
   charges automatic renewals): `manage.py process_billing`, installed by `deploy/cron/install.sh` (see
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
| `process_billing` | Daily 01:15 | Charge automatic renewals, close expired payment links, send trial and renewal reminders |
| `post_depreciation` | Daily 02:00 | Post the fixed asset depreciation of months that have ended (each month once, missed months caught up; organizations with a locked month are skipped and reported) |
| `post_deferrals` | Daily 02:05 | Recognise the months of prepaid expenses and revenue received in advance that have ended (same rules as depreciation) |
| `verify_ledger` | Daily 04:00 | Recompute the ledger hash chain and compare it with the last signed checkpoint, then append a new one (`CHAIN_CHECKPOINT_FILE`); failures are mailed (`MAILTO`) |
| `verify_audit_trail` | Daily 04:10 | The same for the hash chain of the change history |
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

- Nothing open in tax reporting: the SPT Masa PPh 21/26 and Unifikasi have no import format (Coretax builds them from the bukti potong these files create), and the SPT Masa PPN is drafted by Coretax from the fakturs, with the retail total imported from the digunggung file. Coretax's downloads (fakturs, bukti potong) are matched by their column names; check the aliases in `apps/uploads/importers/coretax.py` against a real download when DJP changes its lists.
- Bank statements are read by column name, not per bank: a bank whose download uses other names needs them added to `apps/uploads/importers/bank.py`.

