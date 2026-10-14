"""The audit trail: every insert, change and delete in a tenant table, recorded by the database.

``audit_record_change()`` runs AFTER each row change on the tables in ``TRACKED`` (so only
changes that passed the guards are recorded) and appends one ``audit_recordchange`` row:
the table, the record id, the operation and the data (the new row, the old row, or
``{column: [old, new]}`` for the columns that changed), with the user the application named
for the request (``audit.actor_id``/``audit.actor``, set by ``apps.audit.context``), the
database role, the transaction id and the time. Secrets and bookkeeping columns are left out.

It is SECURITY DEFINER: the app role cannot insert, change or delete trail rows itself
(``apps/core/db_roles.py`` revokes it), so the trail is written only by the database. A
BEFORE INSERT trigger chains each organization's rows like the posted ledger (gap-free
``sequence``, ``previous_hash``, ``change_hash`` = sha256 of the row and the previous hash);
``audit_verify_chain(org)`` (``manage.py verify_audit_trail``) finds a break. Trail rows can
never be updated or deleted, except when the schema owner deletes the whole organization
(``ledger_purge_organization`` sets ``audit.erasing``; ``audit_erasing(org)``).

Also guarded here, since the trail can't stand in for them: document number sequences
(count up one at a time, never released), a previous employer's income (fixed once the
employee's year is settled) and Coretax submissions (kept as sent, with their final result).
"""

import importlib

from django.db import migrations

# (table, where its organization comes from, columns left out of the trail).
# "parent:<table>:<column>" takes the organization of the parent row.
TRACKED = (
    ("tenants_organization", "id"),
    ("tenants_businessplace", "organization_id"),
    ("tenants_membership", "organization_id"),
    ("tenants_invitation", "organization_id", "token_hash"),
    ("ledger_account", "organization_id"),
    ("ledger_journalentry", "organization_id"),
    ("ledger_journalline", "parent:ledger_journalentry:entry_id"),
    ("contacts_contact", "organization_id"),
    ("taxes_taxrate", "organization_id"),
    ("taxes_pphfinalaccrual", "organization_id"),
    ("taxes_pphfinalpayment", "organization_id"),
    ("taxes_withholdingdeposit", "organization_id"),
    ("taxes_importdeclaration", "organization_id"),
    ("taxes_selfdeposit", "organization_id"),
    ("invoicing_item", "organization_id"),
    ("invoicing_invoice", "organization_id"),
    ("invoicing_invoiceline", "parent:invoicing_invoice:invoice_id"),
    ("invoicing_payment", "organization_id"),
    ("invoicing_returnnote", "organization_id"),
    ("invoicing_returnline", "parent:invoicing_returnnote:return_note_id"),
    ("invoicing_coretaxconnection", "organization_id", "token"),
    ("invoicing_coretaxsubmission", "organization_id"),
    ("billing_subscriptioninvoice", "organization_id"),
    ("billing_autorenewal", "organization_id", "payment_token", "setup_url"),
    ("assets_fixedasset", "organization_id"),
    ("assets_assetrevision", "organization_id"),
    ("assets_depreciationposting", "organization_id"),
    ("inventory_stockmovement", "organization_id"),
    ("api_apikey", "organization_id", "key_hash", "last_used_at"),
    ("payroll_payrollsettings", "organization_id"),
    ("payroll_employee", "organization_id"),
    ("payroll_payrollrun", "organization_id"),
    ("payroll_payslip", "parent:payroll_payrollrun:run_id"),
    ("payroll_priorincome", "organization_id"),
)

FUNCTIONS_SQL = r"""
CREATE FUNCTION audit_erasing(org bigint) RETURNS boolean LANGUAGE sql STABLE AS $$
    -- Only while the schema owner deletes this organization (ledger_purge_organization).
    SELECT coalesce(current_setting('audit.erasing', true), '') = org::text
       AND pg_has_role(session_user, (SELECT relowner FROM pg_class WHERE oid = 'audit_recordchange'::regclass), 'USAGE')
$$;

CREATE FUNCTION audit_change_digest(c audit_recordchange) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws(E'\x1f',
        c.previous_hash, c.sequence::text, c.organization_id::text, c.table_name, c.record_id, c.operation,
        c.changes::text, coalesce(c.actor_id::text, ''), c.actor_label, c.db_role, c.transaction_id::text,
        extract(epoch FROM c.changed_at)::text
    ), 'UTF8')), 'hex')
$$;

CREATE FUNCTION audit_chain_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_sequence bigint;
    last_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('audit-chain', NEW.organization_id));
    SELECT c.sequence, c.change_hash INTO last_sequence, last_hash
      FROM audit_recordchange c WHERE c.organization_id = NEW.organization_id
     ORDER BY c.sequence DESC LIMIT 1;
    NEW.sequence := coalesce(last_sequence, 0) + 1;
    NEW.previous_hash := coalesce(last_hash, repeat('0', 64));
    NEW.changed_at := clock_timestamp();
    NEW.transaction_id := txid_current();
    NEW.db_role := session_user;
    NEW.change_hash := audit_change_digest(NEW);
    RETURN NEW;
END
$$;

CREATE FUNCTION audit_guard_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND audit_erasing(OLD.organization_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'The audit trail is append-only.' USING ERRCODE = 'integrity_constraint_violation';
END
$$;

CREATE TRIGGER audit_recordchange_chain BEFORE INSERT ON audit_recordchange
    FOR EACH ROW EXECUTE FUNCTION audit_chain_change();
CREATE TRIGGER audit_recordchange_guard BEFORE UPDATE OR DELETE ON audit_recordchange
    FOR EACH ROW EXECUTE FUNCTION audit_guard_change();

CREATE FUNCTION audit_record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    old_row jsonb;
    new_row jsonb;
    row_data jsonb;
    org bigint;
    diff jsonb;
    org_source text := TG_ARGV[0];
    skip text[] := ARRAY['updated_at'];
    actor text := nullif(current_setting('audit.actor_id', true), '');
    i integer;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_row := to_jsonb(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_row := to_jsonb(NEW);
    END IF;
    row_data := coalesce(new_row, old_row);
    IF org_source LIKE 'parent:%' THEN
        EXECUTE format('SELECT organization_id FROM %I WHERE id = $1', split_part(org_source, ':', 2))
           INTO org USING (row_data ->> split_part(org_source, ':', 3))::bigint;
    ELSE
        org := (row_data ->> org_source)::bigint;
    END IF;
    IF org IS NULL OR audit_erasing(org) THEN
        RETURN NULL;  -- the platform's own rows, or the organization is being deleted
    END IF;
    FOR i IN 1 .. TG_NARGS - 1 LOOP
        skip := skip || TG_ARGV[i];
    END LOOP;
    old_row := old_row - skip;
    new_row := new_row - skip;
    IF TG_OP = 'UPDATE' THEN
        SELECT jsonb_object_agg(n.key, jsonb_build_array(old_row -> n.key, n.value)) INTO diff
          FROM jsonb_each(new_row) n WHERE n.value IS DISTINCT FROM old_row -> n.key;
        IF diff IS NULL THEN
            RETURN NULL;  -- only left-out columns changed
        END IF;
    ELSE
        diff := coalesce(new_row, old_row);
    END IF;
    INSERT INTO audit_recordchange (organization_id, sequence, table_name, record_id, operation, changes, actor_id,
                                    actor_label, db_role, transaction_id, changed_at, previous_hash, change_hash)
    VALUES (org, 0, TG_TABLE_NAME, row_data ->> 'id', left(TG_OP, 1), diff,
            CASE WHEN actor ~ '^[0-9]+$' THEN actor::bigint END,
            left(coalesce(current_setting('audit.actor', true), ''), 254), '', 0, now(), '', '');
    RETURN NULL;
END
$$;

CREATE FUNCTION audit_verify_chain(org bigint)
RETURNS TABLE (out_sequence bigint, out_problem text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    c audit_recordchange%ROWTYPE;
    expected bigint := 0;
    prev text := repeat('0', 64);
BEGIN
    FOR c IN SELECT * FROM audit_recordchange r WHERE r.organization_id = org ORDER BY r.sequence LOOP
        out_sequence := c.sequence;
        expected := expected + 1;
        IF c.sequence <> expected THEN
            out_problem := format('sequence gap: expected %s (changes missing)', expected);
            RETURN NEXT;
            expected := c.sequence;
        END IF;
        IF c.previous_hash <> prev THEN
            out_problem := 'previous hash does not match the preceding change';
            RETURN NEXT;
        END IF;
        IF c.change_hash <> audit_change_digest(c) THEN
            out_problem := 'change altered after it was recorded';
            RETURN NEXT;
        END IF;
        prev := c.change_hash;
    END LOOP;
END
$$;
"""

GUARDS_SQL = r"""
CREATE FUNCTION core_guard_document_sequence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF audit_erasing(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Document numbers are never released.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.last_number <> 0 THEN
            RAISE EXCEPTION 'A document sequence starts at 0.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.organization_id, NEW.prefix, NEW.year, NEW.month, NEW.created_at)
       IS DISTINCT FROM (OLD.organization_id, OLD.prefix, OLD.year, OLD.month, OLD.created_at)
       OR NEW.last_number <> OLD.last_number + 1
    THEN
        RAISE EXCEPTION 'Document numbers only count up, one at a time.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER core_documentsequence_guard BEFORE INSERT OR UPDATE OR DELETE ON core_documentsequence
    FOR EACH ROW EXECUTE FUNCTION core_guard_document_sequence();

CREATE FUNCTION payroll_year_settled(employee bigint, tax_year integer) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM payroll_payslip s JOIN payroll_payrollrun r ON r.id = s.run_id
         WHERE s.employee_id = employee AND r.year = tax_year AND r.status = 'posted' AND s.tax_scheme = 'annual'
    )
$$;
CREATE FUNCTION payroll_guard_prior_income() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' AND audit_erasing(OLD.organization_id) THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF (TG_OP <> 'INSERT' AND payroll_year_settled(OLD.employee_id, OLD.year))
       OR (TG_OP <> 'DELETE' AND payroll_year_settled(NEW.employee_id, NEW.year))
    THEN
        RAISE EXCEPTION 'The employee''s PPh 21 for that year is settled: income from a previous employer is fixed.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN coalesce(NEW, OLD);
END
$$;
CREATE TRIGGER payroll_priorincome_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_priorincome
    FOR EACH ROW EXECUTE FUNCTION payroll_guard_prior_income();

CREATE FUNCTION invoicing_guard_coretax_submission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.organization_id IS NOT NULL AND audit_erasing(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Coretax submission % is kept as sent.', OLD.reference USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF (NEW.organization_id, NEW.document_type, NEW.label, NEW.connector, NEW.reference, NEW.created_at)
       IS DISTINCT FROM (OLD.organization_id, OLD.document_type, OLD.label, OLD.connector, OLD.reference, OLD.created_at)
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
       OR (NEW.records IS DISTINCT FROM OLD.records AND OLD.records <> '[]'::jsonb)
       OR (OLD.status <> 'pending' AND (NEW.status, NEW.results) IS DISTINCT FROM (OLD.status, OLD.results))
    THEN
        RAISE EXCEPTION 'Coretax submission % keeps what was sent and its final result.', OLD.reference
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER invoicing_coretaxsubmission_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_coretaxsubmission
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_coretax_submission();
"""

DROP_GUARDS_SQL = r"""
DROP TRIGGER IF EXISTS invoicing_coretaxsubmission_guard ON invoicing_coretaxsubmission;
DROP FUNCTION IF EXISTS invoicing_guard_coretax_submission();
DROP TRIGGER IF EXISTS payroll_priorincome_guard ON payroll_priorincome;
DROP FUNCTION IF EXISTS payroll_guard_prior_income();
DROP FUNCTION IF EXISTS payroll_year_settled(bigint, integer);
DROP TRIGGER IF EXISTS core_documentsequence_guard ON core_documentsequence;
DROP FUNCTION IF EXISTS core_guard_document_sequence();
"""
DROP_FUNCTIONS_SQL = r"""
DROP FUNCTION IF EXISTS audit_record_change();
DROP FUNCTION IF EXISTS audit_verify_chain(bigint);
DROP TRIGGER IF EXISTS audit_recordchange_guard ON audit_recordchange;
DROP TRIGGER IF EXISTS audit_recordchange_chain ON audit_recordchange;
DROP FUNCTION IF EXISTS audit_guard_change();
DROP FUNCTION IF EXISTS audit_chain_change();
DROP FUNCTION IF EXISTS audit_change_digest(audit_recordchange);
DROP FUNCTION IF EXISTS audit_erasing(bigint);
"""


def track_sql(table: str, organization: str, *left_out: str) -> str:
    """The trigger that records ``table`` in the audit trail (use it for new tenant tables)."""
    args = ", ".join(f"'{arg}'" for arg in (organization, *left_out))
    return (
        f"CREATE TRIGGER audit_trail AFTER INSERT OR UPDATE OR DELETE ON {table}\n"
        f"    FOR EACH ROW EXECUTE FUNCTION audit_record_change({args});\n"
    )


def untrack_sql(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS audit_trail ON {table};\n"


PREVIOUS = importlib.import_module("apps.taxes.migrations.0015_imports_and_self_deposits")


def _purge_function(with_trail: bool) -> str:
    """Deleting an organization (owner only) also removes its trail, and lets the rest of the
    deletion through without recording it or tripping the guards above."""
    sql = PREVIOUS._purge_function(with_documents=True)
    if with_trail:
        assert sql.count(PREVIOUS.FIRST) == 1
        sql = sql.replace(PREVIOUS.FIRST, PREVIOUS.FIRST + (
            "    PERFORM set_config('audit.erasing', org_id::text, true);\n"
            "    DELETE FROM audit_recordchange WHERE organization_id = org_id;\n"
        ))
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0009_record_changes'),
        ('api', '0001_initial'),
        ('assets', '0006_invoiced_disposal'),
        ('billing', '0008_subscription_faktur_number'),
        ('contacts', '0008_structured_address'),
        ('core', '0002_i18n_labels'),
        ('inventory', '0005_write_downs'),
        ('invoicing', '0027_region_snapshots'),
        ('ledger', '0015_pph26_account'),
        ('payroll', '0007_structured_address'),
        ('taxes', '0015_imports_and_self_deposits'),
        ('tenants', '0013_structured_address'),
    ]

    operations = [
        migrations.RunSQL(FUNCTIONS_SQL, DROP_FUNCTIONS_SQL),
        migrations.RunSQL(
            "".join(track_sql(*spec) for spec in TRACKED) + "\n" + GUARDS_SQL,
            "".join(untrack_sql(spec[0]) for spec in TRACKED) + DROP_GUARDS_SQL,
        ),
        migrations.RunSQL(_purge_function(with_trail=True), _purge_function(with_trail=False)),
    ]
