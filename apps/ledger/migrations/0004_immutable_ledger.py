"""Make the ledger immutable and double-entry at the database level.

The service layer already enforces these rules; the triggers make them hold for
every writer (ORM shortcuts, the shell, raw SQL, a future bug):

- an entry is inserted as a draft; the only way to "posted" is an UPDATE from
  draft, which checks the lines: at least two, all accounts of the entry's
  organization, debits = credits > 0, date after the organization's lock date
- a posted entry can never be updated (except clearing ``created_by`` when its
  user is deleted) or deleted
- lines of a posted entry can never be inserted, updated or deleted
- ``SET LOCAL ledger.purge = 'on'`` (``purge_organization_ledger``) lifts the
  guard for offboarding a tenant
"""

from django.conf import settings
from django.db import migrations, models


GUARD_SQL = r"""
CREATE FUNCTION ledger_purge_allowed() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('ledger.purge', true), '') = 'on'
$$;

CREATE FUNCTION ledger_guard_entry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    line_count integer;
    debit_total numeric;
    credit_total numeric;
    org_lock_date date;
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION 'Posted journal entry % cannot be deleted; reverse it instead.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'Journal entries are inserted as drafts and then posted.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.status = 'posted' THEN
        IF (NEW.organization_id, NEW.number, NEW.date, NEW.description, NEW.reference, NEW.status,
            NEW.source, NEW.reversal_of_id, NEW.posted_at, NEW.created_at, NEW.updated_at)
           IS DISTINCT FROM
           (OLD.organization_id, OLD.number, OLD.date, OLD.description, OLD.reference, OLD.status,
            OLD.source, OLD.reversal_of_id, OLD.posted_at, OLD.created_at, OLD.updated_at)
           OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
        THEN
            RAISE EXCEPTION 'Posted journal entry % is immutable; reverse it instead.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status = 'posted' THEN  -- posting a draft
        SELECT count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0)
          INTO line_count, debit_total, credit_total
          FROM ledger_journalline WHERE entry_id = NEW.id;
        IF line_count < 2 OR debit_total <> credit_total OR debit_total <= 0 THEN
            RAISE EXCEPTION 'Journal entry % is not balanced (% lines, debit %, credit %).',
                NEW.number, line_count, debit_total, credit_total
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM ledger_journalline l JOIN ledger_account a ON a.id = l.account_id
             WHERE l.entry_id = NEW.id AND a.organization_id <> NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'Journal entry % uses another organization''s account.', NEW.number
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT lock_date INTO org_lock_date FROM tenants_organization WHERE id = NEW.organization_id;
        IF org_lock_date IS NOT NULL AND NEW.date <= org_lock_date THEN
            RAISE EXCEPTION 'Journal entry % is dated in the locked period (up to %).', NEW.number, org_lock_date
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION ledger_guard_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    entry_status varchar;
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        -- FOR SHARE: wait for a concurrent posting of the same entry to finish
        SELECT status INTO entry_status FROM ledger_journalentry WHERE id = OLD.entry_id FOR SHARE;
        IF entry_status = 'posted' THEN
            RAISE EXCEPTION 'Lines of a posted journal entry cannot be changed or removed.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO entry_status FROM ledger_journalentry WHERE id = NEW.entry_id FOR SHARE;
        IF entry_status = 'posted' THEN
            RAISE EXCEPTION 'Lines cannot be added to a posted journal entry.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN coalesce(NEW, OLD);
END
$$;

CREATE TRIGGER ledger_journalentry_guard
    BEFORE INSERT OR UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_guard_entry();

CREATE TRIGGER ledger_journalline_guard
    BEFORE INSERT OR UPDATE OR DELETE ON ledger_journalline
    FOR EACH ROW EXECUTE FUNCTION ledger_guard_line();
"""

DROP_GUARD_SQL = """
DROP TRIGGER IF EXISTS ledger_journalline_guard ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_journalentry_guard ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_guard_line();
DROP FUNCTION IF EXISTS ledger_guard_entry();
DROP FUNCTION IF EXISTS ledger_purge_allowed();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0003_input_validation'),
        ('tenants', '0007_organization_require_two_factor'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='journalentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('posted_at__isnull', True), ('status', 'draft')), models.Q(('posted_at__isnull', False), ('status', 'posted')), _connector='OR'), name='journal_posted_at_matches_status'),
        ),
        migrations.AddConstraint(
            model_name='journalentry',
            constraint=models.CheckConstraint(condition=models.Q(('reversal_of__isnull', True), ('source', 'reversal'), _connector='OR'), name='journal_reversal_link_only_on_reversals'),
        ),
        migrations.RunSQL(GUARD_SQL, DROP_GUARD_SQL),
    ]
