"""Returns can be voided, once: posted -> void after their journal entry is reversed; nothing
else about them changes. Only posted returns count: towards what an invoice line can still
take back, towards the invoice's ``amount_credited``, and against voiding the invoice."""

import importlib

from django.db import migrations

PREVIOUS = importlib.import_module("apps.invoicing.migrations.0011_immutable_returns")

RETURN_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION invoicing_guard_return() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Return % cannot be deleted; void it instead.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'created_by_id')
           OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
           OR (NEW.status IS DISTINCT FROM OLD.status AND (
               OLD.status <> 'posted' OR NEW.status <> 'void'
               OR NOT EXISTS (SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted')
           ))
        THEN
            RAISE EXCEPTION 'Return % can only be voided, after its journal entry is reversed.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status <> 'posted' OR NOT EXISTS (
        SELECT 1 FROM invoicing_invoice i JOIN ledger_journalentry j ON j.id = NEW.journal_entry_id
         WHERE i.id = NEW.invoice_id AND i.organization_id = NEW.organization_id AND i.kind = NEW.kind
           AND i.status IN ('issued', 'partial', 'paid') AND NEW.date >= i.date
           AND j.organization_id = NEW.organization_id AND j.status = 'posted'
    ) THEN
        RAISE EXCEPTION 'A return is posted for an issued invoice of the same kind, on or after its date.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION invoicing_guard_return_line() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Return lines cannot be changed or deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM invoicing_returnnote rn JOIN invoicing_invoiceline il ON il.id = NEW.invoice_line_id
         WHERE rn.id = NEW.return_note_id AND rn.status = 'posted' AND il.invoice_id = rn.invoice_id
           AND NEW.quantity > 0
           AND NEW.quantity + coalesce((
               SELECT sum(rl.quantity) FROM invoicing_returnline rl JOIN invoicing_returnnote other ON other.id = rl.return_note_id
                WHERE rl.invoice_line_id = il.id AND other.status = 'posted'
           ), 0) <= il.quantity
           AND NEW.amount + coalesce((SELECT sum(rl.amount) FROM invoicing_returnline rl WHERE rl.return_note_id = rn.id), 0) <= rn.subtotal
    ) THEN
        RAISE EXCEPTION 'A return line takes back part of a line of the returned invoice, within what was invoiced and the return''s subtotal.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

INVOICE_GUARD_CHANGES = [
    ("SELECT coalesce(sum(rn.credited), 0) INTO credited FROM invoicing_returnnote rn WHERE rn.invoice_id = NEW.id;",
     "SELECT coalesce(sum(rn.credited), 0) INTO credited FROM invoicing_returnnote rn WHERE rn.invoice_id = NEW.id AND rn.status = 'posted';"),
    ("OR EXISTS (SELECT 1 FROM invoicing_returnnote rn WHERE rn.invoice_id = OLD.id)",
     "OR EXISTS (SELECT 1 FROM invoicing_returnnote rn WHERE rn.invoice_id = OLD.id AND rn.status = 'posted')"),
]


def _invoice_guard(with_voidable_returns: bool) -> str:
    function = PREVIOUS._invoice_guard(with_returns=True)
    if with_voidable_returns:
        for old, new in INVOICE_GUARD_CHANGES:
            assert old in function, old
            function = function.replace(old, new)
    return function


def _previous_return_guards() -> str:
    """0011's two functions, without its CREATE TRIGGER statements."""
    sql, functions, position = PREVIOUS.RETURN_GUARD_SQL, [], 0
    while (start := sql.find("CREATE FUNCTION", position)) != -1:
        end = sql.index("$$;", start) + 3
        functions.append(sql[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        position = end
    assert len(functions) == 2
    return "\n\n".join(functions)


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0012_return_status'),
        ('inventory', '0004_return_voids'),
    ]

    operations = [
        migrations.RunSQL(RETURN_GUARD_SQL, _previous_return_guards()),
        migrations.RunSQL(_invoice_guard(with_voidable_returns=True), _invoice_guard(with_voidable_returns=False)),
    ]
