-- Create (or re-password) the data-only login role the application connects as.
-- Run once per cluster as a superuser, in the application database:
--
--   sudo -u postgres psql -d msme_db -v app_password='...' < deploy/postgres/create_app_role.sql
--
-- Optional: -v app_role=other_name (default msme_app).
-- Table privileges are granted by the schema owner on every `manage.py migrate`
-- (apps/core/db_roles.py), so they also cover tables added later.

\set ON_ERROR_STOP on
\if :{?app_role}
\else
  \set app_role msme_app
\endif
\if :{?app_password}
\else
  \echo 'Pass the password: -v app_password=...'
  \quit
\endif

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
              :'app_role', :'app_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') \gexec

SELECT format('ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
              :'app_role', :'app_password') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_role') \gexec

-- The app role must never create objects (PostgreSQL 15+ already denies this to PUBLIC).
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', :'app_role') \gexec

\echo 'Done. Now set DATABASE_URL to this role, DATABASE_OWNER_URL to the owner, and run manage.py migrate.'
