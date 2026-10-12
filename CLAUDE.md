# BukuUMKM

Django 5.2 + PostgreSQL multi-tenant accounting SaaS for Indonesian MSMEs. See README.md for architecture.

- Run tests: `.venv/bin/pytest` (settings: `config.settings.test`; uses `--reuse-db`, add `--create-db` after model changes)
- Dev server: `.venv/bin/python manage.py runserver`; demo data: `manage.py seed_demo`
- PostgreSQL 18; `msme_user` owns `msme_db` (required to create tables in `public`) and needs CREATEDB for the test DB.

Conventions:
- Business rules in `domain.py` (no ORM), writes in `services.py` (atomic, raise `apps.core.exceptions.DomainError`), reads in `selectors.py`. Views stay thin.
- Every tenant query goes through `Model.objects.for_organization(org)`; views get the org from `OrganizationMixin`/`WriteMixin`/`ManageMixin`.
- All ledger postings go through `ledger.services.create_journal_entry`. Never edit/delete posted entries; reverse them.
- Interface text: English source strings, always marked for translation (`gettext_lazy` for module-level/labels/choices, `gettext` for runtime messages, `{% translate %}`/`{% blocktranslate trimmed %}` in templates). Use `%(name)s` placeholders, never f-strings, and whole phrases rather than fragments. Then `makemessages -l id`, translate in `locale/id/LC_MESSAGES/django.po`, `compilemessages`, commit .po and .mo (a test enforces this).
- Indonesian is the default language; users may pick English (`User.language`, `apps.accounts.middleware`). Stored business data (chart of accounts, journal descriptions, tax-rate/plan names) stays Indonesian and is not translated.
- Format money with the `|rupiah` filter.
- Tests must not rely on data migrations (transactional tests flush the DB); create needed rows in fixtures.
