#!/bin/sh
# Runs once, on first initialisation of the data directory. The SQL lives in /sherpa (the postgres folder, mounted
# read-only) so `make roles` can run the same files on an existing volume.
set -eu
# alpine's /bin/sh is busybox; pipefail is not POSIX there, so enable it only when available.
(set -o pipefail) 2>/dev/null && set -o pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v admin_user="$SHERPA_ADMIN_USER" -v admin_password="$SHERPA_ADMIN_PASSWORD" \
  -v agent_user="$SHERPA_AGENT_USER" -v agent_password="$SHERPA_AGENT_PASSWORD" \
  -v ui_user="$SHERPA_UI_USER" -v ui_password="$SHERPA_UI_PASSWORD" \
  -v db_name="$POSTGRES_DB" -f /sherpa/roles.sql

for database in "$POSTGRES_DB" template1; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" -f /sherpa/large_objects.sql
done
