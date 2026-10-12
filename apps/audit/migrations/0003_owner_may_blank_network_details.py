"""Let the table owner blank IP address and browser on old events (data minimisation).

Organization and subscription events are kept for years, but who-did-what doesn't
need the network details after SECURITY_LOG_RETENTION_DAYS. ``prune_security_logs``
(owner connection) sets ``ip_address`` to NULL and ``user_agent`` to ''. Every other
change stays forbidden for every role, and the app role still cannot touch these
columns at all. Clearing deleted user/organization links remains allowed.
"""

from django.db import migrations

GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION audit_guard_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    is_owner boolean := pg_has_role(
        current_user, (SELECT relowner FROM pg_class WHERE oid = 'audit_auditevent'::regclass), 'USAGE');
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF is_owner THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Audit events cannot be deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.created_at, NEW.action, NEW.actor_email, NEW.organization_name, NEW.target, NEW.details)
       IS DISTINCT FROM
       (OLD.created_at, OLD.action, OLD.actor_email, OLD.organization_name, OLD.target, OLD.details)
       OR (NEW.actor_id IS DISTINCT FROM OLD.actor_id AND NEW.actor_id IS NOT NULL)
       OR (NEW.organization_id IS DISTINCT FROM OLD.organization_id AND NEW.organization_id IS NOT NULL)
       OR (NEW.ip_address IS DISTINCT FROM OLD.ip_address AND (NEW.ip_address IS NOT NULL OR NOT is_owner))
       OR (NEW.user_agent IS DISTINCT FROM OLD.user_agent AND (NEW.user_agent <> '' OR NOT is_owner))
    THEN
        RAISE EXCEPTION 'Audit events cannot be changed.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

PREVIOUS_SQL = r"""
CREATE OR REPLACE FUNCTION audit_guard_event() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0002_append_only'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, PREVIOUS_SQL),
    ]
