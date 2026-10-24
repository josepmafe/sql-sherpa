#!/bin/sh
# Runs once, on first initialisation of the data directory.
set -eu
# alpine's /bin/sh is busybox; pipefail is not POSIX there, so enable it only when available.
(set -o pipefail) 2>/dev/null && set -o pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v admin_user="$SHERPA_ADMIN_USER" -v admin_password="$SHERPA_ADMIN_PASSWORD" \
  -v agent_user="$SHERPA_AGENT_USER" -v agent_password="$SHERPA_AGENT_PASSWORD" \
  -v db_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"admin_user" LOGIN CREATEDB PASSWORD :'admin_password';
ALTER DATABASE :"db_name" OWNER TO :"admin_user";

CREATE ROLE :"agent_user" LOGIN PASSWORD :'agent_password';
-- default_transaction_read_only and statement_timeout are role defaults that a session can override with SET, so
-- they are defence in depth. The real enforcement is the privileges (SELECT only on the marts, granted by dbt), a
-- SQL guard and a read-only connection option in the MCP server.
ALTER ROLE :"agent_user" SET default_transaction_read_only = on;
ALTER ROLE :"agent_user" SET statement_timeout = '5s';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
-- No temporary tables for roles other than the owner (the admin), and no connections to the maintenance database.
REVOKE CREATE, TEMPORARY ON DATABASE :"db_name" FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL

# Large objects are writable storage outside every schema's privileges, so only the superuser may create or write
# them. Function privileges live in each database, so the revoke runs in the main database (already created from
# template1) and in template1, which every later CREATE DATABASE (the eval database) copies.
for database in "$POSTGRES_DB" template1; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" -c \
    'REVOKE EXECUTE ON FUNCTION lo_create(oid), lo_creat(integer), lo_from_bytea(oid, bytea), lo_open(oid, integer),
       lowrite(integer, bytea), lo_put(oid, bigint, bytea) FROM PUBLIC'
done
