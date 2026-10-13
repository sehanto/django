"""Who posted each entry, and a tamper-evident hash chain of posted entries.

When a draft is posted, the database trigger (not the application) assigns the next
gap-free ``posting_sequence`` for the organization and computes::

    entry_hash = sha256(previous_hash | sequence | organization | number | date |
                        description | reference | source | reversal_of | posted_at |
                        lines (account, debit, credit, description) in order)

with ``previous_hash`` = the previous posted entry's hash (64 zeros for the first). A
posted entry can't be changed through the triggers. Someone who bypasses them (the schema
owner or a superuser disabling triggers) breaks the chain, unless they recompute every
later hash. ``ledger_verify_chain(org)`` (``manage.py verify_ledger``) finds such breaks,
and comparing the chain head with one recorded earlier (books export, ``--expect-head``)
catches a full rewrite.

Existing posted entries are chained in posting order. ``posted_by`` is filled in only where
an entry was posted in the same step it was created, so its creator posted it; for
drafts posted later the poster wasn't recorded and stays empty.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DIGEST_SQL = r"""
CREATE FUNCTION ledger_entry_digest(e ledger_journalentry, seq integer, prev text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws(E'\x1f',
        prev, seq::text, e.organization_id::text, e.number, to_char(e.date, 'YYYY-MM-DD'),
        e.description, e.reference, e.source, coalesce(e.reversal_of_id::text, ''),
        extract(epoch FROM e.posted_at)::text,
        coalesce((
            SELECT string_agg(concat_ws(E'\x1e', l.account_id::text, l.debit::text, l.credit::text, l.description),
                              E'\x1d' ORDER BY l.id)
              FROM ledger_journalline l WHERE l.entry_id = e.id
        ), '')
    ), 'UTF8')), 'hex')
$$;
"""

BACKFILL_SQL = r"""
DO $$
DECLARE
    e ledger_journalentry%ROWTYPE;
    current_org bigint;
    seq integer;
    prev text;
BEGIN
    FOR e IN SELECT * FROM ledger_journalentry WHERE status = 'posted' ORDER BY organization_id, posted_at, id LOOP
        IF current_org IS DISTINCT FROM e.organization_id THEN
            current_org := e.organization_id;
            seq := 0;
            prev := repeat('0', 64);
        END IF;
        seq := seq + 1;
        -- The 0004 guard doesn't know these new columns yet, so this backfill passes it.
        UPDATE ledger_journalentry
           SET posting_sequence = seq,
               previous_hash = prev,
               entry_hash = ledger_entry_digest(e, seq, prev),
               posted_by_id = CASE WHEN e.posted_at - e.created_at < interval '5 seconds' THEN e.created_by_id END
         WHERE id = e.id
     RETURNING entry_hash INTO prev;
    END LOOP;
END
$$;
"""

GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION ledger_guard_entry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    line_count integer;
    debit_total numeric;
    credit_total numeric;
    org_lock_date date;
    last_sequence integer;
    last_hash text;
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
        IF NEW.status <> 'draft' OR NEW.posting_sequence IS NOT NULL OR NEW.entry_hash <> '' OR NEW.previous_hash <> '' THEN
            RAISE EXCEPTION 'Journal entries are inserted as drafts and then posted.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.status = 'posted' THEN
        IF (NEW.organization_id, NEW.number, NEW.date, NEW.description, NEW.reference, NEW.status,
            NEW.source, NEW.reversal_of_id, NEW.posted_at, NEW.created_at, NEW.updated_at,
            NEW.posting_sequence, NEW.previous_hash, NEW.entry_hash)
           IS DISTINCT FROM
           (OLD.organization_id, OLD.number, OLD.date, OLD.description, OLD.reference, OLD.status,
            OLD.source, OLD.reversal_of_id, OLD.posted_at, OLD.created_at, OLD.updated_at,
            OLD.posting_sequence, OLD.previous_hash, OLD.entry_hash)
           OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
           OR (NEW.posted_by_id IS DISTINCT FROM OLD.posted_by_id AND NEW.posted_by_id IS NOT NULL)
        THEN
            RAISE EXCEPTION 'Posted journal entry % is immutable; reverse it instead.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status <> 'posted' THEN  -- editing a draft: never part of the chain
        NEW.posting_sequence := NULL;
        NEW.previous_hash := '';
        NEW.entry_hash := '';
        RETURN NEW;
    END IF;

    -- Posting a draft: double-entry checks, then append it to the organization's chain.
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

    -- One posting at a time per organization, so sequences stay gap-free and the chain linear.
    PERFORM pg_advisory_xact_lock(hashtextextended('ledger-chain', NEW.organization_id));
    SELECT j.posting_sequence, j.entry_hash INTO last_sequence, last_hash
      FROM ledger_journalentry j
     WHERE j.organization_id = NEW.organization_id AND j.posting_sequence IS NOT NULL
     ORDER BY j.posting_sequence DESC LIMIT 1;
    NEW.posting_sequence := coalesce(last_sequence, 0) + 1;
    NEW.previous_hash := coalesce(last_hash, repeat('0', 64));
    NEW.entry_hash := ledger_entry_digest(NEW, NEW.posting_sequence, NEW.previous_hash);
    RETURN NEW;
END
$$;

CREATE FUNCTION ledger_verify_chain(org bigint)
RETURNS TABLE (out_sequence integer, out_number text, out_problem text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    e ledger_journalentry%ROWTYPE;
    expected integer := 0;
    prev text := repeat('0', 64);
    line_count integer;
    debit_total numeric;
    credit_total numeric;
BEGIN
    FOR e IN
        SELECT * FROM ledger_journalentry j
         WHERE j.organization_id = org AND (j.status = 'posted' OR j.posting_sequence IS NOT NULL)
         ORDER BY j.posting_sequence NULLS FIRST, j.id
    LOOP
        out_sequence := e.posting_sequence;
        out_number := e.number;
        IF e.status <> 'posted' OR e.posting_sequence IS NULL THEN
            out_problem := 'posted entry outside the chain, or chained entry not posted';
            RETURN NEXT;
            CONTINUE;
        END IF;
        expected := expected + 1;
        IF e.posting_sequence <> expected THEN
            out_problem := format('sequence gap: expected %s (entries missing)', expected);
            RETURN NEXT;
            expected := e.posting_sequence;
        END IF;
        IF e.previous_hash <> prev THEN
            out_problem := 'previous hash does not match the preceding entry';
            RETURN NEXT;
        END IF;
        IF e.entry_hash <> ledger_entry_digest(e, e.posting_sequence, e.previous_hash) THEN
            out_problem := 'content changed after posting';
            RETURN NEXT;
        END IF;
        SELECT count(*), coalesce(sum(l.debit), 0), coalesce(sum(l.credit), 0)
          INTO line_count, debit_total, credit_total
          FROM ledger_journalline l WHERE l.entry_id = e.id;
        IF line_count < 2 OR debit_total <> credit_total OR debit_total <= 0 THEN
            out_problem := format('not balanced (%s lines, debit %s, credit %s)', line_count, debit_total, credit_total);
            RETURN NEXT;
        END IF;
        prev := e.entry_hash;
    END LOOP;
END
$$;
"""

def _previous_guard_sql() -> str:
    """The 0004 version of ledger_guard_entry, restored when this migration is reversed."""
    import importlib

    sql = importlib.import_module("apps.ledger.migrations.0004_immutable_ledger").GUARD_SQL
    start = sql.index("CREATE FUNCTION ledger_guard_entry()")
    end = sql.index("$$;", start) + len("$$;")
    return sql[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


REVERSE_SQL = "DROP FUNCTION IF EXISTS ledger_verify_chain(bigint);\n" + _previous_guard_sql()
DROP_DIGEST_SQL = "DROP FUNCTION IF EXISTS ledger_entry_digest(ledger_journalentry, integer, text);"


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0005_owner_only_purge'),
        ('tenants', '0007_organization_require_two_factor'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='journalentry',
            name='entry_hash',
            field=models.CharField(blank=True, editable=False, max_length=64, verbose_name='entry hash'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='posted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='posted by'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='posting_sequence',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True, verbose_name='posting sequence'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='previous_hash',
            field=models.CharField(blank=True, editable=False, max_length=64, verbose_name='previous hash'),
        ),
        migrations.RunSQL(DIGEST_SQL + BACKFILL_SQL, DROP_DIGEST_SQL),
        migrations.AddConstraint(
            model_name='journalentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('entry_hash', ''), ('posting_sequence__isnull', True), ('status', 'draft')), models.Q(('posting_sequence__isnull', False), ('status', 'posted'), models.Q(('entry_hash', ''), _negated=True)), _connector='OR'), name='journal_chained_when_posted'),
        ),
        migrations.AddConstraint(
            model_name='journalentry',
            constraint=models.UniqueConstraint(condition=models.Q(('posting_sequence__isnull', False)), fields=('organization', 'posting_sequence'), name='unique_journal_posting_sequence'),
        ),
        migrations.RunSQL(GUARD_SQL, REVERSE_SQL),
    ]
