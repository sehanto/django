"""Returns are final, and an invoice's status follows its payments and returns.

* Returns are inserted with a posted journal entry of the same organization, for an issued
  invoice of the same kind, on or after its date; never changed or deleted.
* Return lines take back quantities of that invoice's lines; all returns together never take
  back more than a line's quantity, and a return's lines never add up to more than its subtotal
  (so nothing can be added once it is posted).
* The invoice guard (0004, as 0005-0008 left it) now also checks ``amount_credited`` against the
  posted returns: payments plus returns credited settle the invoice. An invoice with returns
  can't be voided.

``ledger_purge_organization`` removes returns after the stock movements and before the
invoice lines they point to.
"""

import importlib

from django.db import migrations

# Each change to the latest invoice guard (0004, extended by 0005-0008), as (old, new).
INVOICE_GUARD_CHANGES = [
    ("DECLARE\n    paid numeric;", "DECLARE\n    paid numeric;\n    credited numeric;"),
    ("IF NEW.status <> 'draft' OR NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 THEN",
     "IF NEW.status <> 'draft' OR NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 OR NEW.amount_credited <> 0 THEN"),
    ("IF NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 THEN",
     "IF NEW.journal_entry_id IS NOT NULL OR NEW.amount_paid <> 0 OR NEW.amount_credited <> 0 THEN"),
    ("NEW.total <= 0 OR NEW.amount_paid <> 0", "NEW.total <= 0 OR NEW.amount_paid <> 0 OR NEW.amount_credited <> 0"),
    ("(to_jsonb(NEW) - 'status' - 'amount_paid' - 'updated_at' - 'created_by_id')",
     "(to_jsonb(NEW) - 'status' - 'amount_paid' - 'amount_credited' - 'updated_at' - 'created_by_id')"),
    ("(to_jsonb(OLD) - 'status' - 'amount_paid' - 'updated_at' - 'created_by_id')",
     "(to_jsonb(OLD) - 'status' - 'amount_paid' - 'amount_credited' - 'updated_at' - 'created_by_id')"),
    ("IF (NEW.status, NEW.amount_paid) IS NOT DISTINCT FROM (OLD.status, OLD.amount_paid) THEN",
     "IF (NEW.status, NEW.amount_paid, NEW.amount_credited) IS NOT DISTINCT FROM (OLD.status, OLD.amount_paid, OLD.amount_credited) THEN"),
    ("IF OLD.status <> 'issued' OR NEW.amount_paid <> 0 OR NOT EXISTS (",
     "IF OLD.status <> 'issued' OR NEW.amount_paid <> 0 OR NEW.amount_credited <> 0\n"
     "           OR EXISTS (SELECT 1 FROM invoicing_returnnote rn WHERE rn.invoice_id = OLD.id) OR NOT EXISTS ("),
    ("""    IF NEW.amount_paid <> paid
       OR NEW.status IS DISTINCT FROM (CASE WHEN paid <= 0 THEN 'issued' WHEN paid >= NEW.total THEN 'paid' ELSE 'partial' END)
    THEN
        RAISE EXCEPTION 'Invoice % must show its recorded payments (paid %, status %).', OLD.number, paid, NEW.status""",
     """    SELECT coalesce(sum(rn.credited), 0) INTO credited FROM invoicing_returnnote rn WHERE rn.invoice_id = NEW.id;
    IF NEW.amount_paid <> paid OR NEW.amount_credited <> credited
       OR NEW.status IS DISTINCT FROM (
           CASE WHEN paid + credited <= 0 THEN 'issued' WHEN paid + credited >= NEW.total THEN 'paid' ELSE 'partial' END
       )
    THEN
        RAISE EXCEPTION 'Invoice % must show its recorded payments and returns (paid %, credited %, status %).',
            OLD.number, paid, credited, NEW.status"""),
]


def _invoice_guard(with_returns: bool) -> str:
    """The 0008 invoice guard, optionally also settling invoices with returns."""
    function = importlib.import_module("apps.invoicing.migrations.0008_line_item_snapshot")._invoice_guard(with_item_snapshot=True)
    if with_returns:
        for old, new in INVOICE_GUARD_CHANGES:
            assert old in function, old
            function = function.replace(old, new)
    return function


RETURN_GUARD_SQL = r"""
CREATE FUNCTION invoicing_guard_return() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Return % is final and cannot be changed or deleted.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
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

CREATE TRIGGER invoicing_returnnote_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_returnnote
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_return();

CREATE FUNCTION invoicing_guard_return_line() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Return lines cannot be changed or deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM invoicing_returnnote rn JOIN invoicing_invoiceline il ON il.id = NEW.invoice_line_id
         WHERE rn.id = NEW.return_note_id AND il.invoice_id = rn.invoice_id
           AND NEW.quantity > 0
           AND NEW.quantity + coalesce((SELECT sum(rl.quantity) FROM invoicing_returnline rl WHERE rl.invoice_line_id = il.id), 0) <= il.quantity
           AND NEW.amount + coalesce((SELECT sum(rl.amount) FROM invoicing_returnline rl WHERE rl.return_note_id = rn.id), 0) <= rn.subtotal
    ) THEN
        RAISE EXCEPTION 'A return line takes back part of a line of the returned invoice, within what was invoiced and the return''s subtotal.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER invoicing_returnline_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_returnline
    FOR EACH ROW EXECUTE FUNCTION invoicing_guard_return_line();
"""

DROP_RETURN_GUARD_SQL = """
DROP TRIGGER IF EXISTS invoicing_returnline_guard ON invoicing_returnline;
DROP FUNCTION IF EXISTS invoicing_guard_return_line();
DROP TRIGGER IF EXISTS invoicing_returnnote_guard ON invoicing_returnnote;
DROP FUNCTION IF EXISTS invoicing_guard_return();
"""

PAYMENTS = "    DELETE FROM invoicing_payment WHERE organization_id = org_id;\n"


def _purge_function(with_returns: bool) -> str:
    """The inventory 0002 purge function, optionally deleting returns before the payments."""
    sql = importlib.import_module("apps.inventory.migrations.0002_immutable_stock")._purge_function(with_stock=True)
    assert PAYMENTS in sql
    if with_returns:
        sql = sql.replace(PAYMENTS, (
            "    DELETE FROM invoicing_returnline\n"
            "     WHERE return_note_id IN (SELECT id FROM invoicing_returnnote WHERE organization_id = org_id);\n"
            "    DELETE FROM invoicing_returnnote WHERE organization_id = org_id;\n"
        ) + PAYMENTS)
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0010_returns'),
        ('inventory', '0003_returns'),
    ]

    operations = [
        migrations.RunSQL(_invoice_guard(with_returns=True), _invoice_guard(with_returns=False)),
        migrations.RunSQL(RETURN_GUARD_SQL, DROP_RETURN_GUARD_SQL),
        migrations.RunSQL(_purge_function(with_returns=True), _purge_function(with_returns=False)),
    ]
