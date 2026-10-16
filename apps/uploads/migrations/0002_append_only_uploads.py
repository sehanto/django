"""The record of applied uploads is append-only: inserted once, never changed (except that a
deleted user's link is cleared) or deleted, apart from the organization's own deletion
(``ledger_purge_organization`` sets ``audit_erasing``). It is in the audit trail too."""

import importlib

from django.db import migrations

TRAIL = importlib.import_module("apps.audit.migrations.0010_audit_trail")

GUARD_SQL = r"""
CREATE FUNCTION uploads_guard_upload() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF audit_erasing(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'The record of an upload is kept.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (to_jsonb(NEW) - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'created_by_id')
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL) THEN
        RAISE EXCEPTION 'The record of an upload cannot change.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER uploads_upload_guard BEFORE UPDATE OR DELETE ON uploads_upload
    FOR EACH ROW EXECUTE FUNCTION uploads_guard_upload();
""" + TRAIL.track_sql("uploads_upload", "organization_id")

DROP_SQL = TRAIL.untrack_sql("uploads_upload") + """
DROP TRIGGER IF EXISTS uploads_upload_guard ON uploads_upload;
DROP FUNCTION IF EXISTS uploads_guard_upload();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("uploads", "0001_initial"),
        ("audit", "0010_audit_trail"),
    ]

    operations = [migrations.RunSQL(GUARD_SQL, DROP_SQL)]
