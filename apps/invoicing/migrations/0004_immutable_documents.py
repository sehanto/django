"""Issued invoices, their lines and payments are immutable in the database, for every role.

- Invoice: inserted as a draft (no number, journal entry or payment); drafts can be
  edited and deleted. Issuing requires a number, a positive total and a posted journal
  entry of the same organization. After that, every column except status and
  amount_paid is frozen (created_by may only be cleared, when that user is deleted):
    * amount_paid must equal the sum of the invoice's posted payments, and the status
      must follow from it (issued / partial / paid);
    * voiding is allowed only from "issued" with nothing paid, once the invoice's journal
      entry has been reversed; a void invoice is final;
    * an issued invoice is never deleted.
- Invoice lines: added, changed or removed only while their invoice is a draft (clearing
  item_id is allowed: a catalog item was deleted).
- Payment: inserted as posted, for an open invoice, with a posted journal entry; the only
  change is posted -> void once its journal entry has been reversed; never deleted.

The guards are new columns-safe: they compare whole rows minus the columns allowed to
change. Like the ledger, only ``ledger_purge_organization`` (the schema owner, tenant
offboarding) can remove these rows.
"""

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION invoicing_guard_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    paid numeric;
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 THEN
            RAISE EXCEPTION 'Invoices are inserted as drafts and then issued.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'Invoice % has been issued and cannot be deleted; void it instead.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;

    -- UPDATE of a draft: editing, or issuing.
    IF OLD.status = 'draft' THEN
        IF NEW.status = 'draft' THEN
            IF NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 THEN
                RAISE EXCEPTION 'A draft invoice has no journal entry or payments.' USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status <> 'issued' OR NEW.number = '' OR NEW.total <= 0 OR NEW.amount_paid <> 0
           OR NOT EXISTS (
               SELECT 1 FROM ledger_journalentry j
                WHERE j.id = NEW.journal_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id
           )
        THEN
            RAISE EXCEPTION 'An invoice is issued with a number, a positive total and a posted journal entry.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- Issued, paid or void: the document itself is frozen.
    IF (to_jsonb(NEW) - 'status' - 'amount_paid' - 'updated_at' - 'created_by_id')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'amount_paid' - 'updated_at' - 'created_by_id')
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'Invoice % has been issued and cannot be changed; void it instead.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.status, NEW.amount_paid) IS NOT DISTINCT FROM (OLD.status, OLD.amount_paid) THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'void' THEN
        RAISE EXCEPTION 'Invoice % is void.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status = 'void' THEN
        IF OLD.status <> 'issued' OR NEW.amount_paid <> 0 OR NOT EXISTS (
            SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted'
        ) THEN
            RAISE EXCEPTION 'Invoice % can be voided only while unpaid, after its journal entry is reversed.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status NOT IN ('issued', 'partial', 'paid') THEN
        RAISE EXCEPTION 'Invoice % has been issued and cannot return to %.', OLD.number, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT coalesce(sum(p.amount), 0) INTO paid FROM invoicing_payment p WHERE p.invoice_id = NEW.id AND p.status = 'posted';
    IF NEW.amount_paid <> paid
       OR NEW.status IS DISTINCT FROM (CASE WHEN paid <= 0 THEN 'issued' WHEN paid >= NEW.total THEN 'paid' ELSE 'partial' END)
    THEN
        RAISE EXCEPTION 'Invoice % must show its recorded payments (paid %, status %).', OLD.number, paid, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION invoicing_guard_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    invoice_status text;
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.item_id IS NULL
       AND (to_jsonb(NEW) - 'item_id' - 'updated_at') = (to_jsonb(OLD) - 'item_id' - 'updated_at')
    THEN
        RETURN NEW;  -- the catalog item was deleted (SET_NULL); the line keeps its text and amounts
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO invoice_status FROM invoicing_invoice WHERE id = OLD.invoice_id FOR SHARE;
        IF invoice_status <> 'draft' THEN
            RAISE EXCEPTION 'Lines of an issued invoice cannot be changed or removed.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO invoice_status FROM invoicing_invoice WHERE id = NEW.invoice_id FOR SHARE;
        IF invoice_status <> 'draft' THEN
            RAISE EXCEPTION 'Lines cannot be added to an issued invoice.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN coalesce(NEW, OLD);
END
$$;

CREATE FUNCTION invoicing_guard_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Payment % cannot be deleted; void it instead.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'posted' OR NEW.amount <= 0
           OR NOT EXISTS (SELECT 1 FROM invoicing_invoice i WHERE i.id = NEW.invoice_id AND i.status IN ('issued', 'partial')
                                                                 AND i.organization_id = NEW.organization_id)
           OR NOT EXISTS (SELECT 1 FROM ledger_journalentry j WHERE j.id = NEW.journal_entry_id AND j.status = 'posted'
                                                                    AND j.organization_id = NEW.organization_id)
        THEN
            RAISE EXCEPTION 'A payment is recorded for an open invoice with a posted journal entry.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'created_by_id')
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'Payment % cannot be changed; void it instead.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        OLD.status <> 'posted' OR NEW.status <> 'void'
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted')
    ) THEN
        RAISE EXCEPTION 'Payment % can only be voided, after its journal entry is reversed.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER invoicing_invoice_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_invoice();
CREATE TRIGGER invoicing_invoiceline_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_invoiceline
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_line();
CREATE TRIGGER invoicing_payment_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_payment
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_payment();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS invoicing_payment_guard ON invoicing_payment;
DROP TRIGGER IF EXISTS invoicing_invoiceline_guard ON invoicing_invoiceline;
DROP TRIGGER IF EXISTS invoicing_invoice_guard ON invoicing_invoice;
DROP FUNCTION IF EXISTS invoicing_guard_payment();
DROP FUNCTION IF EXISTS invoicing_guard_line();
DROP FUNCTION IF EXISTS invoicing_guard_invoice();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0003_input_validation'),
        ('ledger', '0006_hash_chain'),  # ledger_purge_allowed()
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
    ]
