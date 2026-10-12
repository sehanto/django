# BukuUMKM

Django 5.2 + PostgreSQL multi-tenant accounting SaaS for Indonesian MSMEs. See README.md for architecture.

- Run tests: `.venv/bin/pytest` (settings: `config.settings.test`; uses `--reuse-db`, add `--create-db` after model changes)
- Dev server: `.venv/bin/python manage.py runserver`; demo data: `manage.py seed_demo`
- PostgreSQL 18; `msme_user` owns `msme_db` (required to create tables in `public`) and needs CREATEDB for the test DB.

Conventions:
- Business rules in `domain.py` (no ORM), writes in `services.py` (atomic, raise `apps.core.exceptions.DomainError`), reads in `selectors.py`. Views stay thin.
- Every tenant query goes through `Model.objects.for_organization(org)`; views get the org from `OrganizationMixin`/`WriteMixin`/`ManageMixin`.
- All ledger postings go through `ledger.services.create_journal_entry`. Never edit/delete posted entries; reverse them.
- Language policy is in README "Languages"; follow it. URLs are English and language-neutral; never put the language in the URL.
- Interface text: English source strings, always marked for translation (`gettext_lazy` for module-level/labels/choices, `gettext` for runtime messages, `{% translate %}`/`{% blocktranslate trimmed %}` in templates). Use `%(name)s` placeholders, whole sentences, `ngettext`/`{% blocktranslate count %}` for anything counted, `pgettext` for ambiguous short words. Then `makemessages -l id`, translate in `locale/id/LC_MESSAGES/django.po`, `compilemessages`, commit .po and .mo (a test enforces this).
- Money: `|rupiah` in templates, `apps.core.formatting.localized_rupiah` in messages (separators follow the active language). Customer-facing documents render in `Contact.document_language`.
- Stored business data (chart of accounts, journal descriptions, tax-rate/plan names) stays Indonesian (UU KUP Art. 28(4)) and is never translated.
- Plan changes only happen through `apps.billing` (payment confirmed by re-fetching the gateway, never by trusting a webhook body or redirect). `tenants.services.set_subscription` is the single writer of `Organization.plan`/`plan_paid_until`. Dev/tests use `BILLING_GATEWAY=fake`; schedule `manage.py process_billing` daily.
- Tests must not rely on data migrations (transactional tests flush the DB); create needed rows in fixtures.
