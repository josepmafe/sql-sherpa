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
-- they are defence in depth. The real enforcement is the privileges (SELECT only on the marts, granted in a later
-- milestone), a SQL guard and a read-only connection option in the MCP server.
ALTER ROLE :"agent_user" SET default_transaction_read_only = on;
ALTER ROLE :"agent_user" SET statement_timeout = '5s';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON DATABASE :"db_name" FROM PUBLIC;
SQL
