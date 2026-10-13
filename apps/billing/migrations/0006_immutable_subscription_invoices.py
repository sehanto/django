"""Subscription invoices (our own sales records) are immutable in the database.

For every role, including the data-only app role:

- The commercial terms never change after insert: plan, period, buyer snapshot,
  amounts, PPN, proration credit, gateway, created_at.
- INSERT: "pending", or "paid" only for a plan switch fully covered by credit
  (gateway "credit", total 0, paid_at and coverage set).
- The temporary number given on insert ("tmp-...") is replaced once by the final
  "SUB-..." number.
- While pending: the checkout details (gateway reference, payment link, expiry) can be set.
- Status: pending -> paid / expired / canceled / failed; expired, canceled or failed
  -> paid (a bank transfer confirmed by support); paid is final.
- Becoming paid sets paid_at (required), the gateway payment ID and the coverage; these
  can't change afterwards.
- A paid invoice may only get its "ended early on" date (superseded_on), once.
- organization and created_by may only be cleared (SET_NULL when deleted): the sales
  record survives the tenant.
- Never deleted.
"""

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION billing_guard_subscription_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    -- Columns that may change after insert, each under the rules below.
    mutable text[] := ARRAY['number', 'status', 'gateway_reference', 'payment_url', 'expires_at', 'gateway_payment_id',
                            'paid_at', 'coverage_start', 'coverage_end', 'coverage_value', 'superseded_on',
                            'organization_id', 'created_by_id', 'updated_at'];
    becoming_paid boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Subscription invoice % is a sales record and cannot be deleted.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status = 'pending' THEN
            IF NEW.paid_at IS NOT NULL OR NEW.gateway_payment_id <> '' OR NEW.superseded_on IS NOT NULL THEN
                RAISE EXCEPTION 'A new subscription invoice is unpaid.' USING ERRCODE = 'integrity_constraint_violation';
            END IF;
        ELSIF NOT (NEW.status = 'paid' AND NEW.gateway = 'credit' AND NEW.total = 0 AND NEW.paid_at IS NOT NULL
                   AND NEW.coverage_end IS NOT NULL AND NEW.superseded_on IS NULL) THEN
            RAISE EXCEPTION 'Subscription invoices start pending (or paid, for a switch covered by credit).'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: the commercial terms are frozen.
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'Subscription invoice % cannot be changed: its terms are fixed.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.number IS DISTINCT FROM OLD.number AND NOT (OLD.number LIKE 'tmp-%' AND NEW.number LIKE 'SUB-%') THEN
        RAISE EXCEPTION 'Subscription invoice % cannot be renumbered.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.organization_id IS DISTINCT FROM OLD.organization_id AND NEW.organization_id IS NOT NULL)
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Subscription invoice % cannot be moved to another organization or author.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.gateway_reference, NEW.payment_url, NEW.expires_at) IS DISTINCT FROM (OLD.gateway_reference, OLD.payment_url, OLD.expires_at)
       AND OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'The checkout of subscription invoice % is closed.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'pending' AND NEW.status IN ('paid', 'expired', 'canceled', 'failed'))
        OR (OLD.status IN ('expired', 'canceled', 'failed') AND NEW.status = 'paid')
    ) THEN
        RAISE EXCEPTION 'Subscription invoice % cannot go from % to %.', OLD.number, OLD.status, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    becoming_paid := OLD.status <> 'paid' AND NEW.status = 'paid';
    IF becoming_paid AND NEW.paid_at IS NULL THEN
        RAISE EXCEPTION 'A paid subscription invoice records when it was paid.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT becoming_paid AND (NEW.paid_at, NEW.gateway_payment_id, NEW.coverage_start, NEW.coverage_end, NEW.coverage_value)
       IS DISTINCT FROM (OLD.paid_at, OLD.gateway_payment_id, OLD.coverage_start, OLD.coverage_end, OLD.coverage_value) THEN
        RAISE EXCEPTION 'The payment and coverage of subscription invoice % are recorded once, when it is paid.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.superseded_on IS DISTINCT FROM OLD.superseded_on
       AND NOT (OLD.superseded_on IS NULL AND NEW.status = 'paid' AND OLD.status = 'paid') THEN
        RAISE EXCEPTION 'Only a paid subscription invoice can end early, once.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER billing_subscriptioninvoice_guard BEFORE INSERT OR UPDATE OR DELETE ON billing_subscriptioninvoice
    FOR EACH ROW EXECUTE FUNCTION billing_guard_subscription_invoice();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS billing_subscriptioninvoice_guard ON billing_subscriptioninvoice;
DROP FUNCTION IF EXISTS billing_guard_subscription_invoice();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_gateway_choices'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
    ]
