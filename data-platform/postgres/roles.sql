-- Creates the three roles and the PUBLIC revokes, or brings an existing database up to date: a re-run resets
-- passwords and role settings and never fails. Run by 00_roles.sh on first initialisation and by `make up`.
-- Needs the psql variables admin_user, admin_password, agent_user, agent_password, ui_user, ui_password, db_name.

-- CREATE ROLE has no IF NOT EXISTS, so each role is created or has its password reset by a generated statement.
SELECT format(
  CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = :'admin_user')
    THEN 'ALTER ROLE %I LOGIN CREATEDB PASSWORD %L'
    ELSE 'CREATE ROLE %I LOGIN CREATEDB PASSWORD %L' END,
  :'admin_user', :'admin_password') \gexec
ALTER DATABASE :"db_name" OWNER TO :"admin_user";

SELECT format(
  CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = :'agent_user')
    THEN 'ALTER ROLE %I LOGIN PASSWORD %L'
    ELSE 'CREATE ROLE %I LOGIN PASSWORD %L' END,
  :'agent_user', :'agent_password') \gexec
-- At most 20 sessions at once: each MCP server holds a pool of a few, and a runaway client cannot take them all.
ALTER ROLE :"agent_user" CONNECTION LIMIT 20;
-- default_transaction_read_only and statement_timeout are role defaults that a session can override with SET, so
-- they are defence in depth. The real enforcement is the privileges (SELECT only on the marts, granted by dbt), a
-- SQL guard and a read-only connection option in the MCP server.
ALTER ROLE :"agent_user" SET default_transaction_read_only = on;
ALTER ROLE :"agent_user" SET statement_timeout = '5s';
-- Caps the temporary files one query may write (a huge sort or hash); only a superuser can change it, not a session.
ALTER ROLE :"agent_user" SET temp_file_limit = '256MB';

SELECT format(
  CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = :'ui_user')
    THEN 'ALTER ROLE %I LOGIN PASSWORD %L'
    ELSE 'CREATE ROLE %I LOGIN PASSWORD %L' END,
  :'ui_user', :'ui_password') \gexec
-- The web backend's own reader (the Explorer's tables and samples): fewer sessions than the agent, a longer timeout.
ALTER ROLE :"ui_user" CONNECTION LIMIT 10;
ALTER ROLE :"ui_user" SET default_transaction_read_only = on;
ALTER ROLE :"ui_user" SET statement_timeout = '30s';
ALTER ROLE :"ui_user" SET temp_file_limit = '256MB';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
-- No temporary tables for roles other than the owner (the admin), and no connections to the maintenance database.
REVOKE CREATE, TEMPORARY ON DATABASE :"db_name" FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
