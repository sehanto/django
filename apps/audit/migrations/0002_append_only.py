"""Make the audit log append-only in the database, for every role.

- INSERT: always allowed.
- UPDATE: only clearing ``actor_id``/``organization_id`` (Django's SET_NULL when a
  user or organization is deleted); the email/name snapshots keep the record readable.
- DELETE: only the owner of the table (retention pruning: ``manage.py prune_security_logs``).
  The data-only app role can never remove or rewrite events.
"""

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION audit_guard_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_has_role(current_user, (SELECT relowner FROM pg_class WHERE oid = 'audit_auditevent'::regclass), 'USAGE') THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Audit events cannot be deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.created_at, NEW.action, NEW.actor_email, NEW.organization_name, NEW.ip_address, NEW.user_agent,
        NEW.target, NEW.details)
       IS DISTINCT FROM
       (OLD.created_at, OLD.action, OLD.actor_email, OLD.organization_name, OLD.ip_address, OLD.user_agent,
        OLD.target, OLD.details)
       OR (NEW.actor_id IS DISTINCT FROM OLD.actor_id AND NEW.actor_id IS NOT NULL)
       OR (NEW.organization_id IS DISTINCT FROM OLD.organization_id AND NEW.organization_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'Audit events cannot be changed.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER audit_auditevent_guard
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_guard_event();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS audit_auditevent_guard ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_guard_event();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
    ]
