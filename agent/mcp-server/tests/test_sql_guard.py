from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from sherpa_mcp.sql_guard import ALLOWED_FUNCTIONS, SqlGuard

if TYPE_CHECKING:
    import psycopg
    from sherpa_mcp.settings import McpSettings

MART_COLUMNS = {
    "fct_orders": frozenset(
        {"order_id", "customer_id", "order_date", "net_revenue", "channel", "country_code", "is_first_order"}
    ),
    "dim_customers": frozenset({"customer_id", "email", "first_name", "customer_segment"}),
    "fct_order_items": frozenset({"order_id", "variant_id", "quantity", "net_amount"}),
}
MARTS_HINT = "query only the marts that list_tables returns"
FUNCTION_HINT = "rewrite it with the common aggregate, window, string, date and math functions"
# sqlglot node names that are not the Postgres spelling of a function
SQLGLOT_NODE_NAME = re.compile(
    r"\b(j_s_o_n\w*|unix_to_time|rand|exploding_generate_series|json_extract_scalar|array_contains_all|array_size"
    r"|current_version|regexp_like)\b"
)

# (sql, rule, message): the rule code and the message before the hint, as the model reads them
REJECTED_QUERIES = [
    # one statement only
    ("select 1 from marts.fct_orders; drop table x", "statement_count", "expected exactly one statement, got 2"),
    (
        "select 1 from marts.fct_orders -- c\n; delete from marts.fct_orders",
        "statement_count",
        "expected exactly one statement, got 2",
    ),
    (
        "select 'a\\' ; delete from marts.fct_orders; --' from marts.fct_orders",
        "statement_count",
        "expected exactly one statement, got 2",
    ),
    ("", "statement_count", "expected exactly one statement, got 0"),
    ("-- only a comment", "statement_count", "expected exactly one statement, got 0"),
    # DML, DDL and session changes; the name is the SQL keyword the model reads
    ("delete from marts.fct_orders", "not_select", "the statement must be a SELECT query, not DELETE"),
    ("update marts.fct_orders set net_revenue = 0", "not_select", "the statement must be a SELECT query, not UPDATE"),
    ("insert into marts.fct_orders values (1)", "not_select", "the statement must be a SELECT query, not INSERT"),
    (
        "merge into marts.fct_orders t using marts.dim_customers s on true when matched then delete",
        "not_select",
        "the statement must be a SELECT query, not MERGE",
    ),
    ("create table x as select 1", "not_select", "the statement must be a SELECT query, not CREATE"),
    ("drop table marts.fct_orders", "not_select", "the statement must be a SELECT query, not DROP"),
    ("truncate marts.fct_orders", "not_select", "the statement must be a SELECT query, not TRUNCATE"),
    ("set statement_timeout = 0", "not_select", "the statement must be a SELECT query, not SET"),
    ("reset all", "not_select", "the statement must be a SELECT query, not RESET"),
    (
        "alter role sherpa_agent set default_transaction_read_only = off",
        "not_select",
        "the statement must be a SELECT query, not ALTER",
    ),
    ("set role postgres", "not_select", "the statement must be a SELECT query, not SET"),
    ("set session authorization postgres", "not_select", "the statement must be a SELECT query, not SET"),
    ("do $$ begin perform pg_sleep(10); end $$", "not_select", "the statement must be a SELECT query, not DO"),
    ("explain analyze delete from marts.fct_orders", "not_select", "the statement must be a SELECT query, not EXPLAIN"),
    ("prepare p as select 1", "not_select", "the statement must be a SELECT query, not PREPARE"),
    ("execute p", "not_select", "the statement must be a SELECT query, not EXECUTE"),
    ("listen x", "not_select", "the statement must be a SELECT query, not LISTEN"),
    ("notify x", "not_select", "the statement must be a SELECT query, not NOTIFY"),
    ("call proc()", "not_select", "the statement must be a SELECT query, not CALL"),
    ("vacuum", "not_select", "the statement must be a SELECT query, not VACUUM"),
    ("lock table marts.fct_orders", "not_select", "the statement must be a SELECT query, not LOCK"),
    ("discard all", "not_select", "the statement must be a SELECT query, not DISCARD"),
    ("table pg_roles", "not_select", "the statement must be a SELECT query, not TABLE"),
    ("values (1)", "not_select", "the statement must be a SELECT query, not VALUES"),
    ("copy marts.fct_orders to stdout", "not_select", "the statement must be a SELECT query, not COPY"),
    ("copy (select 1) to program 'id'", "not_select", "the statement must be a SELECT query, not COPY"),
    ("begin", "not_select", "the statement must be a SELECT query, not BEGIN"),
    ("start transaction", "not_select", "the statement must be a SELECT query, not START"),
    ("checkpoint", "not_select", "the statement must be a SELECT query, not CHECKPOINT"),
    ("abort", "not_select", "the statement must be a SELECT query, not ABORT"),
    ("cluster", "not_select", "the statement must be a SELECT query, not CLUSTER"),
    ("grant select on marts.fct_orders to public", "not_select", "the statement must be a SELECT query, not GRANT"),
    # writes hidden inside a query
    ("select * into x from marts.fct_orders", "select_into", "SELECT ... INTO is not allowed"),
    ("select * from (select * into t from marts.fct_orders) s", "select_into", "SELECT ... INTO is not allowed"),
    ("with d as (delete from marts.fct_orders returning *) select * from d", "write", "DELETE is not allowed"),
    ("with u as (update marts.fct_orders set a = 1 returning *) select * from u", "write", "UPDATE is not allowed"),
    (
        "with i as (insert into marts.fct_orders values (1) returning *) select * from i",
        "write",
        "INSERT is not allowed",
    ),
    (
        "with d as (merge into marts.fct_orders t using marts.dim_customers s on true when matched then delete) "
        "select 1",
        "write",
        "MERGE is not allowed",
    ),
    ("select * from marts.fct_orders for update", "locking", "locking clause FOR UPDATE/SHARE is not allowed"),
    ("select * from marts.fct_orders for share", "locking", "locking clause FOR UPDATE/SHARE is not allowed"),
    (
        "select * from (select * from marts.fct_orders for no key update) s",
        "locking",
        "locking clause FOR UPDATE/SHARE is not allowed",
    ),
    # functions outside the allow-list
    ("select pg_sleep(10)", "function", "function pg_sleep is not allowed"),
    ("select query_to_xml('select 1', true, true, '')", "function", "function query_to_xml is not allowed"),
    ("select table_to_xml('t', true, true, '')", "function", "function table_to_xml is not allowed"),
    ("select cursor_to_xml('c', 1, true, true, '')", "function", "function cursor_to_xml is not allowed"),
    ("select current_setting('x')", "function", "function current_setting is not allowed"),
    ("select set_config('a', 'b', false)", "function", "function set_config is not allowed"),
    ("select * from dblink('host=x', 'select 1') as t(a int)", "function", "function dblink is not allowed"),
    ("select dblink_exec('host=x', 'drop table y')", "function", "function dblink_exec is not allowed"),
    ("select lo_import('/etc/passwd')", "function", "function lo_import is not allowed"),
    ("select pg_read_file('/etc/passwd')", "function", "function pg_read_file is not allowed"),
    ("select * from pg_ls_dir('.')", "function", "function pg_ls_dir is not allowed"),
    ("select * from pg_read_file('/etc/passwd') as t(x)", "function", "function pg_read_file is not allowed"),
    ("select txid_current()", "function", "function txid_current is not allowed"),
    ("select row_to_json(o) from marts.fct_orders o", "function", "function row_to_json is not allowed"),
    ("select current_user", "function", "function current_user is not allowed"),
    ("select version()", "function", "function version is not allowed"),
    ('select "pg_sleep"(1)', "function", "function pg_sleep is not allowed"),
    (
        "select 1 from marts.fct_orders where net_revenue > 0 and pg_sleep(1) is null",
        "function",
        "function pg_sleep is not allowed",
    ),
    ("select 1 from marts.fct_orders order by (select pg_sleep(1))", "function", "function pg_sleep is not allowed"),
    # casts to reg* types look names up in the system catalogs
    ("select 10::regrole::text from marts.fct_orders", "cast", "cast to type REGROLE is not allowed"),
    ("select cast('pg_authid' as regclass) from marts.fct_orders", "cast", "cast to type REGCLASS is not allowed"),
    ("select 'x'::regnamespace", "cast", "cast to type REGNAMESPACE is not allowed"),
    ("select 1::pg_catalog.regclass", "cast", "cast to type pg_catalog.regclass is not allowed"),
    ("select * from generate_series(1, 3)", "function", "function generate_series is not allowed"),
    # table functions in FROM
    ("select * from unnest(array[1, 2])", "function", "function unnest is not allowed"),
    (
        "select * from rows from (generate_series(1, 2))",
        "table",
        "table ROWS FROM (GENERATE_SERIES(1, 2)) is not allowed",
    ),
    (
        "select * from marts.fct_orders o cross join lateral generate_series(1, 2) g",
        "function",
        "function generate_series is not allowed",
    ),
    # schema-qualified functions, even allowed ones
    ("select pg_catalog.pg_sleep(1)", "function", "function pg_catalog.pg_sleep is not allowed"),
    ("select pg_catalog.lower('a') from marts.fct_orders", "function", "function pg_catalog.lower is not allowed"),
    ("select marts.lower('a') from marts.fct_orders", "function", "function marts.lower is not allowed"),
    ("select * from pg_catalog.lower('a')", "function", "function pg_catalog.lower is not allowed"),
    ("select * from public.lower('a')", "function", "function public.lower is not allowed"),
    ("select * from marts.upper('a') u", "function", "function marts.upper is not allowed"),
    ("select * from sherpa.public.lower('a')", "function", "function sherpa.public.lower is not allowed"),
    # tables outside the visible marts
    ("select * from pg_catalog.pg_roles", "table", "table pg_catalog.pg_roles is outside marts"),
    ("select * from information_schema.tables", "table", "table information_schema.tables is outside marts"),
    ("select * from fct_orders", "table", "table fct_orders is not schema-qualified"),
    ("select * from pg_roles", "table", "table pg_roles is not schema-qualified"),
    ('select * from "marts.fct_orders"', "table", "table marts.fct_orders is not schema-qualified"),
    ("select * from staging.stg_shop__orders", "table", "table staging.stg_shop__orders is outside marts"),
    # the dev target's schemas hold the same marts under another name
    ("select * from dev_marts.dim_customers", "table", "table dev_marts.dim_customers is outside marts"),
    ("select * from marts_dev.dim_customers", "table", "table marts_dev.dim_customers is outside marts"),
    ('select * from "dev_marts"."fct_orders"', "table", "table dev_marts.fct_orders is outside marts"),
    ("select * from marts.dim_customers_v2", "table", "table marts.dim_customers_v2 is not a visible mart"),
    ('select * from marts."fct_orders "', "table", "table marts.fct_orders  is not a visible mart"),
    ("select * from sherpa.marts.fct_orders", "table", "table sherpa.marts.fct_orders is not allowed"),
    ("select * from a.b.c.d", "table", "table a.b.c.d is not allowed"),
    ("select * from marts.fct_orders, pg_catalog.pg_user", "table", "table pg_catalog.pg_user is outside marts"),
    ("select * from marts.fct_orders join pg_roles on true", "table", "table pg_roles is not schema-qualified"),
    (
        "select * from marts.fct_orders natural join pg_catalog.pg_class",
        "table",
        "table pg_catalog.pg_class is outside marts",
    ),
    (
        "select * from marts.fct_orders o, lateral (select * from pg_catalog.pg_roles) r",
        "table",
        "table pg_catalog.pg_roles is outside marts",
    ),
    (
        "select (select rolpassword from pg_authid limit 1) from marts.fct_orders",
        "table",
        "table pg_authid is not schema-qualified",
    ),
    (
        "select 1 from marts.fct_orders where exists (select 1 from pg_catalog.pg_shadow)",
        "table",
        "table pg_catalog.pg_shadow is outside marts",
    ),
    ("select 1 from marts.fct_orders union select 2 from pg_roles", "table", "table pg_roles is not schema-qualified"),
    (
        "select * from marts.fct_orders tablesample bernoulli (10), pg_catalog.pg_proc",
        "table",
        "table pg_catalog.pg_proc is outside marts",
    ),
    (
        "select a from (select a from (select a from pg_catalog.pg_stats) x) y",
        "table",
        "table pg_catalog.pg_stats is outside marts",
    ),
    (
        "select * from (values (1)) v(a) join pg_catalog.pg_roles r on true",
        "table",
        "table pg_catalog.pg_roles is outside marts",
    ),
    # CTE names only cover references Postgres resolves to the CTE
    ('with "PG_ROLES" as (select 1) select * from pg_roles', "table", "table pg_roles is not schema-qualified"),
    (
        "with pg_roles as (select * from pg_roles) select * from pg_roles",
        "table",
        "table pg_roles is not schema-qualified",
    ),
    (
        "with a as (select * from pg_roles), pg_roles as (select 1) select * from a",
        "table",
        "table pg_roles is not schema-qualified",
    ),
    (
        "select * from (with pg_roles as (select 1) select * from pg_roles) s, pg_roles",
        "table",
        "table pg_roles is not schema-qualified",
    ),
    (
        "select 1 where 1 in (with x as (select 1) select * from x) and 1 in (select * from x)",
        "table",
        "table x is not schema-qualified",
    ),
    ("(with x as (select 1) select * from x) union select * from x", "table", "table x is not schema-qualified"),
    # OPERATOR(schema.op) names any operator, past the rules for the operators written out
    (
        "select 1 from marts.dim_customers where email OPERATOR(pg_catalog.~) '^a'",
        "operator",
        "operator call `email OPERATOR(pg_catalog.~) '^a'` is not allowed",
    ),
    (
        "select 1 from marts.fct_orders where order_id operator(=) 1",
        "operator",
        "operator call `order_id OPERATOR(=) 1` is not allowed",
    ),
    (
        "select 1 from marts.dim_customers where (email) operator(pg_catalog.~~) 'a%'",
        "operator",
        "operator call `(email) OPERATOR(pg_catalog.~~) 'a%'` is not allowed",
    ),
    (
        "select order_id operator(pg_catalog.+) 1 from marts.fct_orders",
        "operator",
        "operator call `order_id OPERATOR(pg_catalog.+) 1` is not allowed",
    ),
    # parameters nobody binds: Postgres refuses `$1`, and sqlglot reads Postgres's prefix `@` as one
    (
        "select $1 from marts.fct_orders",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    (
        "select 1 from marts.fct_orders where order_id = $1",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    ("select $1::int", "parameter", "query parameters ($1, ?, :name) and the prefix @ operator are not allowed"),
    (
        "select @order_id from marts.fct_orders",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    (
        "select ? from marts.fct_orders",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    (
        "select 1 from marts.fct_orders where order_id = :id",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    (
        "select %s from marts.fct_orders",
        "parameter",
        "query parameters ($1, ?, :name) and the prefix @ operator are not allowed",
    ),
    # parser differentials and malformed input
    ("select 1 from marts.fct_orders # 1", "parse", "could not parse the query"),
    ('select * from U&"pg_\\0072oles"', "parse", "could not parse the query"),
    ("select xmlparse(document '<a/>')", "parse", "could not parse the query"),
    ("select " + "(" * 2000 + "1" + ")" * 2000, "parse", "could not parse the query"),
    # functions named as written in Postgres, not as sqlglot names them
    ("select json_agg(order_id) from marts.fct_orders", "function", "function json_agg is not allowed"),
    ("select to_timestamp(0) from marts.fct_orders", "function", "function to_timestamp is not allowed"),
    ("select random() from marts.fct_orders", "function", "function random is not allowed"),
    ("select * from public.generate_series(1, 3)", "function", "function generate_series is not allowed"),
    ("select json_table(a) from marts.fct_orders", "function", "function json_table is not allowed"),
    ("select array_length(a, 1) from marts.fct_orders", "function", "function array_length is not allowed"),
    ("select current_user from marts.fct_orders", "function", "function current_user is not allowed"),
    ("select localtimestamp from marts.fct_orders", "function", "function localtimestamp is not allowed"),
    ("select grouping(a) from marts.fct_orders group by a", "function", "function grouping is not allowed"),
    ("select a ->> 'k' from marts.fct_orders", "function", "function or operator `a ->> 'k'` is not allowed"),
    ("select a @> b from marts.fct_orders", "function", "function or operator `a @> b` is not allowed"),
    ("select 1 from marts.fct_orders where a ~ 'x'", "function", "function or operator `a ~ 'x'` is not allowed"),
    (
        "select 1 from marts.fct_orders where a = any(array[1, 2])",
        "function",
        "function or operator `ARRAY[1, 2]` is not allowed",
    ),
    (
        "select a ->> '" + "k" * 80 + "' from marts.fct_orders",
        "function",
        "function or operator `" + ("a ->> '" + "k" * 80)[:60] + "...` is not allowed",
    ),
    # whole-row references, record conversions and field selections
    ("select c from marts.dim_customers c", "whole_row", "whole-row reference `c` is not allowed"),
    ("select c from marts.dim_customers as c", "whole_row", "whole-row reference `c` is not allowed"),
    (
        "select dim_customers from marts.dim_customers",
        "whole_row",
        "whole-row reference `dim_customers` is not allowed",
    ),
    ("select C from marts.dim_customers c", "whole_row", "whole-row reference `C` is not allowed"),
    (
        "select o.order_id, c from marts.fct_orders o join marts.dim_customers c on c.customer_id = o.customer_id",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    ("with x as (select 1 as a) select x from x", "whole_row", "whole-row reference `x` is not allowed"),
    (
        "select c from (marts.fct_orders o join marts.dim_customers c on c.customer_id = o.customer_id) limit 1",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select c::text as t from (marts.dim_customers c cross join marts.fct_order_items d)",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select c from marts.fct_orders o join (marts.dim_customers c join marts.fct_order_items d on true) on true",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select c from (marts.fct_orders o join (marts.dim_customers c join marts.fct_order_items d on true) on true)",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select c from ((marts.dim_customers c join marts.fct_order_items d on true))",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select d from (marts.dim_customers c join marts.fct_order_items d on true)",
        "whole_row",
        "whole-row reference `d` is not allowed",
    ),
    (
        "select c.*::text from (marts.dim_customers c join marts.fct_order_items d on true)",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "with x as (select email from marts.dim_customers) select x from (marts.fct_orders o join x on true)",
        "whole_row",
        "whole-row reference `x` is not allowed",
    ),
    (
        "with revenue as (select sum(net_revenue) as revenue from marts.fct_orders) select revenue from revenue",
        "whole_row",
        "whole-row reference `revenue` is not allowed",
    ),
    ("select s from (select email from marts.dim_customers) s", "whole_row", "whole-row reference `s` is not allowed"),
    (
        "select * from marts.fct_orders where exists (select c from marts.dim_customers c)",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    # an alias of an outer query read from a subquery
    (
        "select (select c from marts.fct_orders o limit 1) from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    (
        "select x.v from marts.dim_customers c, lateral (select c as v) x",
        "whole_row",
        "whole-row reference `c` is not allowed",
    ),
    ("select to_json(c) from marts.dim_customers c", "function", "function to_json is not allowed"),
    ("select row_to_json(c) from marts.dim_customers c", "function", "function row_to_json is not allowed"),
    ("select row(c.email) from marts.dim_customers c", "function", "function row is not allowed"),
    ("select c::text from marts.dim_customers c", "whole_row", "whole-row reference `c` is not allowed"),
    # a qualified star inside an expression returns the whole row
    (
        "select c.*::text from marts.dim_customers c limit 1",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select concat(c.*) from marts.dim_customers c limit 1",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select (c.*)::text as x from marts.dim_customers c limit 1",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select array_agg(c.*) from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select coalesce(c.*) from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.* || 'x' from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.* is null from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select count(c.*) from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    # Postgres expands `c.* as x` and ignores the alias; the guard fails closed on it anyway
    (
        "select c.* as x from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.email from marts.dim_customers c order by c.*",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.email from marts.dim_customers c where c.* is not null",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.email from marts.dim_customers c where (c.*) = (c.*)",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select count(*) from marts.dim_customers c group by c.*",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select count(*) from marts.dim_customers c group by 1 having count(c.*) > 0",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select distinct on (c.*) c.email from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select count(*) over (partition by c.*) from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select x from (select c.*::text as x from marts.dim_customers c) s",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "with t as (select c.*::text as x from marts.dim_customers c) select x from t",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        "select c.* from marts.dim_customers c union select c.*::text from marts.dim_customers c",
        "whole_row",
        "whole-row reference `c.*` inside an expression is not allowed",
    ),
    (
        'select "C".*::text from marts.dim_customers "C"',
        "whole_row",
        'whole-row reference `"C".*` inside an expression is not allowed',
    ),
    (
        "select dim_customers.*::text from marts.dim_customers",
        "whole_row",
        "whole-row reference `dim_customers.*` inside an expression is not allowed",
    ),
    (
        "select marts.dim_customers.*::text from marts.dim_customers",
        "whole_row",
        "whole-row reference `marts.dim_customers.*` inside an expression is not allowed",
    ),
    ("select row_to_json(c.*) from marts.dim_customers c", "function", "function row_to_json is not allowed"),
    ("select to_jsonb(c.*) from marts.dim_customers c", "function", "function to_jsonb is not allowed"),
    ("select (c).* from marts.dim_customers c", "whole_row", "whole-row reference `c` is not allowed"),
    ("select (c).email from marts.dim_customers c", "whole_row", "whole-row reference `c` is not allowed"),
    # quoted names match case-insensitively; Postgres reads unquoted `Foo` as foo, and the guard fails closed on it too
    ('select "C" from marts.dim_customers "C"', "whole_row", "whole-row reference `C` is not allowed"),
    ('select "Foo" from marts.dim_customers as "Foo"', "whole_row", "whole-row reference `Foo` is not allowed"),
    (
        'select "dim_customers" from marts.dim_customers',
        "whole_row",
        "whole-row reference `dim_customers` is not allowed",
    ),
    (
        'select "DIM_CUSTOMERS" from marts.dim_customers',
        "whole_row",
        "whole-row reference `DIM_CUSTOMERS` is not allowed",
    ),
    ('with "X" as (select 1) select "X" from "X"', "whole_row", "whole-row reference `X` is not allowed"),
    ('select Foo from marts.dim_customers as "Foo"', "whole_row", "whole-row reference `Foo` is not allowed"),
    ('select "C" from marts.dim_customers c', "whole_row", "whole-row reference `C` is not allowed"),
    ("select 1 from", "parse", "could not parse the query"),
    ("select (c.email).x from marts.dim_customers c", "field", "field selection `(c.email).x` is not allowed"),
    ("select c.nope from marts.dim_customers c", "column", "column c.nope is not a column of marts.dim_customers"),
    ("select 1::regclass from marts.fct_orders", "cast", "cast to type REGCLASS is not allowed"),
    (
        "select 1 from marts.fct_orders where order_id operator(pg_catalog.=) 1",
        "operator",
        "operator call `order_id OPERATOR(pg_catalog.=) 1` is not allowed",
    ),
    (
        "select jsonb_build_object('a', 1) from marts.fct_orders",
        "function",
        "function jsonb_build_object is not allowed",
    ),
    ("select grouping(channel) from marts.fct_orders group by channel", "function", "function grouping is not allowed"),
    ("select array_to_json(order_id) from marts.fct_orders", "function", "function array_to_json is not allowed"),
    ("(select 1 from marts.fct_orders)", "not_select", "the statement must be a SELECT query, not SUBQUERY"),
]

# (sql, rule, hint): what the model is told to write instead
REMEDY_HINTS = [
    (
        "select 1 from marts.fct_orders where order_id = any(array[1, 2])",
        "function",
        "use IN (...) instead of = ANY(ARRAY[...])",
    ),
    (
        "select 1 from marts.fct_orders where channel ~ 'web'",
        "function",
        "use LIKE or ILIKE instead of a regular expression",
    ),
    (
        "select 1 from marts.fct_orders where channel ~* 'web'",
        "function",
        "use LIKE or ILIKE instead of a regular expression",
    ),
    ("(select 1 from marts.fct_orders)", "not_select", "remove the outer parentheses"),
    ("select * from generate_series(1, 3)", "function", "use marts.dim_date for a series of dates"),
    ("select * from public.lower('a')", "function", "call functions without a schema"),
    ("select to_timestamp(0) from marts.fct_orders", "function", "use to_char, make_date or date_trunc"),
    ("select json_agg(order_id) from marts.fct_orders", "function", "aggregate with string_agg, or select the columns"),
    (
        "select jsonb_build_object('a', 1) from marts.fct_orders",
        "function",
        "aggregate with string_agg, or select the columns",
    ),
    ("select row_to_json(o) from marts.fct_orders o", "function", "aggregate with string_agg, or select the columns"),
    (
        "select grouping(channel) from marts.fct_orders group by channel",
        "function",
        "use a plain GROUP BY, or one query per grouping level",
    ),
    (
        "select random() from marts.fct_orders",
        "function",
        "rewrite it with the common aggregate, window, string, date and math functions",
    ),
    ("select channel @> 'x' from marts.fct_orders", "function", "use comparison operators, LIKE/ILIKE or IN (...)"),
    (
        "select 1 from marts.fct_orders where order_id operator(=) 1",
        "operator",
        "use comparison operators, LIKE/ILIKE or IN (...)",
    ),
    (
        "select $1 from marts.fct_orders",
        "parameter",
        "write the values into the query, and abs(x) for an absolute value",
    ),
    (
        "select array_to_json(order_id) from marts.fct_orders",
        "function",
        "aggregate with string_agg, or select the columns",
    ),
    ("select * from fct_orders", "table", "qualify it as marts.fct_orders"),
    ("select * from pg_catalog.pg_roles", "table", "query only the marts that list_tables returns"),
    ("select * from a.b.c.d", "table", "use marts.<name>"),
    (
        "select c from marts.dim_customers c",
        "whole_row",
        "select named columns, or qualify a column that shares a table, alias or CTE name (`t.name`)",
    ),
    (
        "with revenue as (select sum(net_revenue) as revenue from marts.fct_orders) select revenue from revenue",
        "whole_row",
        "select named columns, or qualify a column that shares a table, alias or CTE name (`t.name`)",
    ),
    ("select c.*::text from marts.dim_customers c", "whole_row", "select named columns"),
    ("select (c.email).x from marts.dim_customers c", "field", "select named columns"),
    ("select c.nope from marts.dim_customers c", "column", "use a column listed by describe_table"),
    ("select 1 from", "parse", "write one Postgres SELECT"),
    ("select 1 from marts.fct_orders; select 2", "statement_count", "send one SELECT"),
    ("delete from marts.fct_orders", "not_select", "write one SELECT"),
    ("select * into x from marts.fct_orders", "select_into", "remove INTO"),
    ("select * from marts.fct_orders for update", "locking", "remove it"),
    ("with d as (delete from marts.fct_orders returning *) select * from d", "write", "only SELECT queries run"),
    (
        "select 1::regclass from marts.fct_orders",
        "cast",
        "cast to a built-in type such as text, numeric, date or timestamp",
    ),
    (
        "with recursive n as (select 1) select * from n",
        "recursive",
        "use marts.dim_date for a series of dates, or a non-recursive query",
    ),
    (
        "select lpad('a', 99999) from marts.fct_orders",
        "pad",
        "write the length as a number, e.g. lpad(x::text, 2, '0')",
    ),
    (
        "select 1_000 from marts.fct_orders",
        "number",
        "write plain decimal digits such as 10000, without _ separators or a 0x, 0o or 0b prefix",
    ),
    ("select ''::char(20000) from marts.fct_orders", "type_length", "use lengths up to 10,000, or cast to text"),
    ("select pg_catalog.lower('a') from marts.fct_orders", "function", "call functions without a schema"),
]


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard(MART_COLUMNS)


class TestAllowedFunctions:
    def test_the_allow_list_is_exactly_the_specs(self) -> None:
        # Agent spec §4.2, in sqlglot's names where it parses a Postgres function into another node
        expected = frozenset(
            {
                # aggregates
                *("count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop", "variance"),
                *("var_samp", "var_pop", "variance_pop", "percentile_cont", "percentile_disc", "mode"),
                *("string_agg", "group_concat", "array_agg", "bool_and", "bool_or", "logical_and", "logical_or"),
                # window
                *("row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value"),
                *("nth_value", "percent_rank", "cume_dist"),
                # math
                *("abs", "round", "ceil", "ceiling", "floor", "trunc", "power", "pow", "sqrt", "ln", "log", "exp"),
                *("mod", "sign", "greatest", "least"),
                # string
                *("lower", "upper", "length", "char_length", "substring", "substr", "trim", "ltrim", "rtrim"),
                *("replace", "concat", "concat_ws", "left", "right", "split_part", "strpos", "position"),
                *("str_position", "lpad", "rpad", "pad", "initcap"),
                # date and time
                *("date_trunc", "timestamp_trunc", "date_part", "extract", "age", "now", "current_date"),
                *("current_timestamp", "to_char", "time_to_str", "to_date", "str_to_date", "make_date", "date_bin"),
                # conditional and casts
                *("coalesce", "nullif", "case", "if", "cast", "try_cast"),
                # trigram
                "similarity",
            }
        )

        assert expected == ALLOWED_FUNCTIONS

    @pytest.mark.parametrize(
        "name",
        [
            # the families the spec names, three or more members each
            *("pg_terminate_backend", "pg_read_file", "pg_sleep", "pg_ls_dir", "pg_backend_pid"),
            *("dblink_connect", "dblink", "dblink_exec"),
            *("lo_create", "lo_import", "lo_export"),
            *("query_to_xml", "table_to_xml", "cursor_to_xml", "current_setting", "set_config"),
            *("setval", "nextval", "currval"),
            "inet_server_addr",
        ],
    )
    def test_a_rejected_family_member_is_refused_by_name(self, guard: SqlGuard, name: str) -> None:
        result = guard.check(f"select {name}(1) from marts.fct_orders")

        assert any(e.startswith(f"[guard:function] function {name} is not allowed") for e in result.errors), (
            result.errors
        )

    def test_a_rejected_function_is_refused_in_any_case(self, guard: SqlGuard) -> None:
        result = guard.check("select PG_Sleep(1) from marts.fct_orders")

        assert result.errors[0].startswith("[guard:function] function pg_sleep is not allowed")

    @pytest.mark.parametrize(
        "call", ["SIMILARITY(channel, 'web')", "MAKE_DATE(2025, 1, 1)", "Date_Trunc('day', order_date)"]
    )
    def test_an_allowed_function_is_accepted_in_any_case(self, guard: SqlGuard, call: str) -> None:
        result = guard.check(f"select {call} from marts.fct_orders")

        assert result.errors == ()

    def test_allowed_functions_can_be_replaced(self) -> None:
        narrow = SqlGuard(MART_COLUMNS, allowed_functions=frozenset({"count"}))
        wide = SqlGuard(MART_COLUMNS, allowed_functions=ALLOWED_FUNCTIONS | {"md5"})

        assert narrow.check("select count(*) from marts.fct_orders").ok
        assert not narrow.check("select upper(channel) from marts.fct_orders").ok
        assert wide.check("select md5(channel) from marts.fct_orders").ok
        assert not SqlGuard(MART_COLUMNS).check("select md5(channel) from marts.fct_orders").ok


@pytest.mark.integration
class TestFunctionsThePostgresRoleCanRun:
    def test_the_guard_accepts_exactly_the_specs_functions_among_them(
        self, guard: SqlGuard, admin: psycopg.Connection, warehouse_settings: McpSettings
    ) -> None:
        # Every function the agent role may execute, by a name Postgres reads unquoted, called with 0 to 4 literal
        # arguments. The accepted names are the spec's list (agent spec §4.2) as Postgres spells it, plus a few other
        # spellings that sqlglot parses into an allowed node or operator.
        names = [
            row[0]
            for row in admin.execute(
                "select distinct proname from pg_proc"
                " where has_function_privilege(%s, oid, 'EXECUTE') and proname ~ '^[a-z_][a-z0-9_]*$' order by 1",
                (warehouse_settings.agent_user,),
            )
        ]

        accepted = {name for name in names if any(guard.check(_call(name, arguments)).ok for arguments in range(5))}

        assert accepted == {
            # aggregates
            *("count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop", "variance"),
            *("var_samp", "var_pop", "percentile_cont", "percentile_disc", "mode"),
            *("string_agg", "array_agg", "bool_and", "bool_or"),
            # window
            *("row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value", "nth_value"),
            *("percent_rank", "cume_dist"),
            # math
            *("abs", "round", "ceil", "ceiling", "floor", "trunc", "power", "pow", "sqrt", "ln", "log", "exp", "mod"),
            *("sign",),
            # string
            *("lower", "upper", "length", "char_length", "substring", "substr", "ltrim", "rtrim", "replace"),
            *("concat", "concat_ws", "left", "right", "split_part", "strpos", "position", "lpad", "rpad", "initcap"),
            # date
            *("date_trunc", "date_part", "extract", "age", "now", "to_char", "to_date", "make_date", "date_bin"),
            # trigram
            "similarity",
            # other spellings of the above that sqlglot parses into an allowed node or operator
            *("btrim", "character_length", "div", "interval", "like", "log10"),
        }


class TestSqlGuard:
    def test_accepts_plain_select(self, guard: SqlGuard) -> None:
        result = guard.check("select order_id, net_revenue from marts.fct_orders where order_date >= '2025-01-01'")

        assert result.ok
        assert result.errors == ()

    def test_accepts_window_and_aggregates(self, guard: SqlGuard) -> None:
        sql = """
            select
                date_trunc('month', order_date) as month,
                count(*) filter (where is_first_order) as first_orders,
                count(distinct customer_id) as customers,
                sum(net_revenue) as revenue,
                round(avg(net_revenue), 2) as aov,
                string_agg(country_code, ',') as countries,
                percentile_cont(0.5) within group (order by net_revenue) as median_revenue,
                rank() over (order by sum(net_revenue) desc) as revenue_rank,
                lag(sum(net_revenue)) over (order by date_trunc('month', order_date)) as previous_revenue,
                coalesce(nullif(max(channel), ''), 'unknown') as channel,
                case when sum(net_revenue) > 1000 then 'big' else 'small' end as size,
                extract(year from max(order_date))::int as year,
                date_part('dow', min(order_date)) as dow,
                to_char(max(order_date), 'YYYY-MM') as label,
                greatest(max(net_revenue), 0) - least(min(net_revenue), 0) as spread,
                bool_and(is_first_order) and bool_or(is_first_order) as mixed,
                similarity(max(channel), 'web') as channel_similarity,
                upper(left(max(country_code), 2)) || lower(trim(max(channel))) as tag,
                age(current_date, min(order_date)) as tenure,
                now() - interval '1 day' as yesterday
            from marts.fct_orders
            where exists (select 1 from marts.dim_customers c where c.customer_id = fct_orders.customer_id)
                and (channel ilike 'web%' or channel is null)
                and net_revenue between 0 and 1000
                and country_code in ('ES', 'DE')
            group by 1
            order by 1
            limit 100
        """

        result = guard.check(sql)

        assert result.errors == ()
        assert result.ok

    @pytest.mark.parametrize(
        "call",
        [
            *("count(*)", "sum(a)", "avg(a)", "min(a)", "max(a)", "stddev(a)", "stddev_samp(a)", "stddev_pop(a)"),
            *("variance(a)", "var_samp(a)", "var_pop(a)", "percentile_cont(0.5) within group (order by a)"),
            *("percentile_disc(0.5) within group (order by a)", "mode() within group (order by a)"),
            *("string_agg(b, ',')", "array_agg(a)", "bool_and(c)", "bool_or(c)"),
            *("row_number() over ()", "rank() over ()", "dense_rank() over ()", "ntile(4) over ()"),
            *("lag(a) over ()", "lead(a, 2) over ()", "first_value(a) over ()", "last_value(a) over ()"),
            *("nth_value(a, 2) over ()", "percent_rank() over ()", "cume_dist() over ()"),
            *("abs(a)", "round(a, 2)", "ceil(a)", "ceiling(a)", "floor(a)", "trunc(a)", "power(a, 2)", "pow(a, 2)"),
            *("sqrt(a)", "ln(a)", "log(a)", "exp(a)", "mod(a, 2)", "sign(a)", "greatest(a, 1)", "least(a, 1)"),
            *("lower(b)", "upper(b)", "length(b)", "char_length(b)", "substring(b from 1 for 2)", "substr(b, 1)"),
            *("trim(b)", "ltrim(b)", "rtrim(b)", "replace(b, 'x', 'y')", "concat(b, 'x')", "concat_ws(',', b)"),
            *("left(b, 2)", "right(b, 2)", "split_part(b, ',', 1)", "strpos(b, 'x')", "position('x' in b)"),
            *("lpad(b, 5)", "rpad(b, 5, '-')", "initcap(b)"),
            *("date_trunc('month', d)", "date_part('year', d)", "extract(dow from d)", "age(d)", "now()"),
            *("current_date", "current_timestamp", "to_char(d, 'YYYY')", "to_date('2025-01-01', 'YYYY-MM-DD')"),
            *("make_date(2025, 1, 1)", "date_bin('1 day', d, timestamp '2025-01-01')"),
            *("coalesce(a, 0)", "nullif(a, 0)", "case when c then 1 else 0 end", "cast(a as int)", "a::int"),
            "similarity(b, 'x')",
        ],
    )
    def test_accepts_every_allowed_function(self, guard: SqlGuard, call: str) -> None:
        result = guard.check(f"select {call} from marts.fct_orders")

        assert result.errors == ()

    def test_accepts_casts_to_built_in_types(self, guard: SqlGuard) -> None:
        sql = """
            select order_id::text, net_revenue::numeric(12, 2), order_date::timestamptz, cast(order_id as bigint),
                is_first_order::boolean, channel::varchar(10), net_revenue::double precision, date '2025-01-01',
                '{1, 2}'::int[], '1 day'::interval
            from marts.fct_orders
        """

        result = guard.check(sql)

        assert result.errors == ()

    def test_accepts_union_of_selects(self, guard: SqlGuard) -> None:
        sql = """
            select customer_id from marts.fct_orders
            union all
            select customer_id from marts.dim_customers
            except
            select customer_id from marts.fct_order_items
        """

        result = guard.check(sql)

        assert result.ok

    def test_accepts_intersect_of_selects(self, guard: SqlGuard) -> None:
        sql = """
            select customer_id from marts.fct_orders
            intersect
            select customer_id from marts.dim_customers
        """

        result = guard.check(sql)

        assert result.ok

    def test_cte_references_are_allowed_unqualified(self, guard: SqlGuard) -> None:
        sql = """
            with monthly as (
                select date_trunc('month', order_date) as month, sum(net_revenue) as revenue
                from marts.fct_orders
                group by 1
            ),
            ranked as (select month, revenue, row_number() over (order by revenue desc) as rn from monthly)
            select * from ranked where rn <= 3
        """

        result = guard.check(sql)

        assert result.ok
        assert result.tables == ("marts.fct_orders",)

    @pytest.mark.parametrize(
        "sql",
        [
            "with recursive n as (select 1 as i union all select i + 1 from n where i < 5) select * from n",
            "with recursive n as (select 1 as i) select * from n",
            "select * from (with recursive n as (select 1 as i union all select i + 1 from n) select * from n) s",
            "select * from marts.fct_orders where order_id in (with recursive n(i) as (select 1) select i from n)",
        ],
    )
    def test_recursive_cte_is_rejected(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert (
            "[guard:recursive] WITH RECURSIVE is not allowed; "
            "use marts.dim_date for a series of dates, or a non-recursive query"
        ) in result.errors

    @pytest.mark.parametrize(
        "call",
        ["lpad(channel, 2, '0')", "lpad(order_id::text, 8, '0')", "rpad(channel, 10)", "lpad(channel, 10000, '-')"],
    )
    def test_pad_with_a_small_literal_length_passes(self, guard: SqlGuard, call: str) -> None:
        result = guard.check(f"select {call} from marts.fct_orders")

        assert result.ok, result.errors

    @pytest.mark.parametrize(
        ("call", "name"),
        [
            ("lpad('a', 100000000, 'b')", "lpad"),
            ("rpad('a', 10001)", "rpad"),
            ("lpad('a', order_id::int)", "lpad"),
            ("lpad('a', 5 * 1000)", "lpad"),
            ("lpad('a', (5))", "lpad"),
            ("lpad('a', '5')", "lpad"),
            ("lpad('a', 5.0)", "lpad"),
            ("rpad('a', -1)", "rpad"),
            ("lpad('a', (select max(order_id) from marts.fct_orders)::int)", "lpad"),
        ],
    )
    def test_pad_with_a_computed_or_huge_length_is_rejected(self, guard: SqlGuard, call: str, name: str) -> None:
        result = guard.check(f"select {call} from marts.fct_orders")

        assert result.errors == (
            f"[guard:pad] {name} needs a literal length of at most 10,000; "
            f"write the length as a number, e.g. {name}(x::text, 2, '0')",
        )

    @pytest.mark.parametrize(
        ("sql", "type_sql"),
        [
            ("select 1::bit(83886080) from marts.fct_orders", "BIT(83886080)"),
            ("select ''::char(10485760) from marts.fct_orders", "CHAR(10485760)"),
            ("select cast(channel as varchar(10001)) from marts.fct_orders", "VARCHAR(10001)"),
            ("select channel::character varying(20000) from marts.fct_orders", "VARCHAR(20000)"),
            ("select channel::bpchar(20000) from marts.fct_orders", "BPCHAR(20000)"),
            ("select channel::nchar(20000) from marts.fct_orders", "CHAR(20000)"),
            ("select char(20000) 'a' from marts.fct_orders", "CHAR(20000)"),
            ("select channel::varchar(20000)[] from marts.fct_orders", "VARCHAR(20000)"),
            ("select channel::char(1e7) from marts.fct_orders", "CHAR(1e7)"),
            ("select 1 from marts.fct_orders where channel = ''::char(10485760)", "CHAR(10485760)"),
        ],
    )
    def test_type_length_over_the_cell_cap_is_rejected(self, guard: SqlGuard, sql: str, type_sql: str) -> None:
        result = guard.check(sql)

        assert result.errors == (
            f"[guard:type_length] type {type_sql} has a length over 10,000; use lengths up to 10,000, or cast to text",
        )

    @pytest.mark.parametrize(
        "cast",
        [
            "net_revenue::numeric(12, 2)",
            "cast(net_revenue as numeric(38, 4))",
            "channel::varchar(10000)",
            "channel::char(2)",
            "1::bit(8)",
            "order_date::timestamp(3)",
            "char(10) 'a'",
        ],
    )
    def test_type_length_up_to_the_cell_cap_passes(self, guard: SqlGuard, cast: str) -> None:
        result = guard.check(f"select {cast} from marts.fct_orders")

        assert result.ok, result.errors

    @pytest.mark.parametrize(
        ("sql", "literal"),
        [
            # Postgres 16 reads these as one number; sqlglot cuts them short or reads something else
            ("select ''::char(10_485_760) from marts.fct_orders", "10_485_760"),
            ("select 1::bit(83_886_080) from marts.fct_orders", "83_886_080"),
            ("select cast('' as char(10_485_760)) from marts.fct_orders", "10_485_760"),
            ("select char(10_485_760) 'a' from marts.fct_orders", "10_485_760"),
            ("select char(0o47040) 'a' from marts.fct_orders", "0o47040"),
            ("select ''::char(0O47040) from marts.fct_orders", "0O47040"),
            ("select ''::char(0x989680) from marts.fct_orders", "0x989680"),
            ("select ''::char(0X989680) from marts.fct_orders", "0X989680"),
            ("select ''::char(0b100110001001011010000000) from marts.fct_orders", "0b100110001001011010000000"),
            ("select ''::char(0x_98_96_80) from marts.fct_orders", "0x_98_96_80"),
            ("select ''::char(1_0e4) from marts.fct_orders", "1_0e4"),
            ("select ''::char(1__0) from marts.fct_orders", "1__0"),
            ("select net_revenue::numeric(1_000, 2) from marts.fct_orders", "1_000"),
            ("select lpad('a', 1_000_000) from marts.fct_orders", "1_000_000"),
            ("select rpad('a', 5_000, 'b') from marts.fct_orders", "5_000"),
            ("select lpad('a', 0x100000) from marts.fct_orders", "0x100000"),
            ("select rpad('a', 0o4000000) from marts.fct_orders", "0o4000000"),
            ("select 1_000 from marts.fct_orders", "1_000"),
            ("select order_id from marts.fct_orders where order_id = 1_000.5", "1_000"),
            ("select 0x10 from marts.fct_orders", "0x10"),
            ("select 0b from marts.fct_orders", "0b"),
        ],
    )
    def test_numbers_postgres_reads_differently_are_rejected(self, guard: SqlGuard, sql: str, literal: str) -> None:
        result = guard.check(sql)

        assert result.errors == (
            f"[guard:number] numeric literal `{literal}` is not allowed; "
            "write plain decimal digits such as 10000, without _ separators or a 0x, 0o or 0b prefix",
        )

    @pytest.mark.parametrize(
        "sql",
        [
            "select net_revenue, customer_id from marts.fct_orders where channel in ('1_000', '0x10', '0o7 0b1')",
            'select order_id as x1_2, order_id as col_1_2, order_id as "a1_2", order_id as "1_000" '
            "from marts.fct_orders",
            "select 10::int, 1e4, 1.5e3, .5, 5., 1.e3, 2e-3, x'1f', b'101' from marts.fct_orders",
            "select order_id from marts.fct_orders -- 1_000 0x10\nwhere order_id > 0 /* 2_000 0b1 */ limit 10",
            "select $$1_000$$, $q$0x10$q$, E'0o7\\'1_0' from marts.fct_orders",
            "select order_id+1, order_id-1, order_id*2, lpad(channel, 10, '0') from marts.fct_orders",
        ],
    )
    def test_names_strings_and_comments_with_digits_and_underscores_pass(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors

    def test_repeat_stays_rejected(self, guard: SqlGuard) -> None:
        result = guard.check("select repeat('a', 5) from marts.fct_orders")

        assert not result.ok

    def test_identifier_case_and_quotes(self, guard: SqlGuard) -> None:
        quoted = guard.check('select * from "marts"."fct_orders"')
        mixed_case = guard.check("SELECT * FROM MARTS.Fct_Orders")
        other_schema = guard.check('select * from "staging"."fct_orders"')

        assert quoted.ok
        assert mixed_case.ok
        assert quoted.tables == mixed_case.tables == ("marts.fct_orders",)
        assert not other_schema.ok
        assert f"[guard:table] table staging.fct_orders is outside marts; {MARTS_HINT}" in other_schema.errors

    def test_quoted_identifiers_keep_their_case(self, guard: SqlGuard) -> None:
        # Postgres folds unquoted names to lower case but keeps quoted ones verbatim, so these name other relations.
        upper_table = guard.check('select * from marts."FCT_ORDERS"')
        upper_schema = guard.check('select * from "MARTS".fct_orders')

        assert upper_table.errors == (f"[guard:table] table marts.FCT_ORDERS is not a visible mart; {MARTS_HINT}",)
        assert upper_schema.errors == (f"[guard:table] table MARTS.fct_orders is outside marts; {MARTS_HINT}",)

    def test_trailing_semicolon_and_comment_ok(self, guard: SqlGuard) -> None:
        sql = "/* top */ select 1 from marts.fct_orders -- why\n; -- trailing comment\n"

        result = guard.check(sql)

        assert result.ok

    def test_reports_tables_referenced(self, guard: SqlGuard) -> None:
        sql = """
            select o.order_id
            from marts.fct_order_items i
            join marts.fct_orders o using (order_id)
            join MARTS.FCT_ORDERS o2 using (order_id)
            where o.customer_id in (select customer_id from marts.dim_customers)
        """

        result = guard.check(sql)

        assert result.tables == ("marts.fct_order_items", "marts.fct_orders", "marts.dim_customers")

    @pytest.mark.parametrize(("sql", "rule", "fragment"), REJECTED_QUERIES)
    def test_rejects_with_its_rule_code_and_message(self, guard: SqlGuard, sql: str, rule: str, fragment: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any(_says(e, rule, fragment) for e in result.errors), result.errors
        assert all(re.match(r"\[guard:\w+\] ", e) for e in result.errors), result.errors
        assert SQLGLOT_NODE_NAME.search(" ".join(result.errors)) is None

    @pytest.mark.parametrize(
        "sql",
        [
            "select $$ ; delete from marts.fct_orders; $$ from marts.fct_orders",
            "select E'a\\' ; delete from marts.fct_orders; --' from marts.fct_orders",
            "select 1 /* /* */ ; delete from marts.fct_orders; /* */ */ from marts.fct_orders",
            "select '--' as dashes, 'pg_sleep(1)' as text from marts.fct_orders",
            "select * from marts.fct_orders as pg_roles",
            "select * from marts.fct_orders tablesample bernoulli (10)",
            "select * from (values (1), (2)) v(a)",
            "select * from marts.fct_orders o, "
            "lateral (select * from marts.dim_customers c where c.customer_id = o.customer_id) c",
        ],
    )
    def test_accepts_hostile_looking_text_that_postgres_reads_as_one_safe_query(
        self, guard: SqlGuard, sql: str
    ) -> None:
        result = guard.check(sql)

        assert result.errors == ()

    def test_lists_every_violation(self, guard: SqlGuard) -> None:
        result = guard.check("select pg_sleep(1) from staging.stg_shop__orders")

        assert result.errors == (
            f"[guard:function] function pg_sleep is not allowed; {FUNCTION_HINT}",
            f"[guard:table] table staging.stg_shop__orders is outside marts; {MARTS_HINT}",
        )

    def test_repeated_violation_is_reported_once(self, guard: SqlGuard) -> None:
        result = guard.check("select pg_sleep(1), pg_sleep(2) from pg_catalog.pg_roles a, pg_catalog.pg_roles b")

        assert result.errors == (
            f"[guard:function] function pg_sleep is not allowed; {FUNCTION_HINT}",
            f"[guard:table] table pg_catalog.pg_roles is outside marts; {MARTS_HINT}",
        )

    @pytest.mark.parametrize(("sql", "rule", "hint"), REMEDY_HINTS)
    def test_rejection_ends_with_its_remedy_hint(self, guard: SqlGuard, sql: str, rule: str, hint: str) -> None:
        result = guard.check(sql)

        assert any(e.startswith(f"[guard:{rule}] ") and e.endswith(f"; {hint}") for e in result.errors), result.errors

    @pytest.mark.parametrize(
        ("sql", "calls"),
        [
            ("select now() - interval '1 day' from marts.fct_orders", ("now()/current_timestamp",)),
            ("select 1 from marts.fct_orders where order_date >= current_date - 30", ("current_date",)),
            (
                "select current_timestamp, current_date, now() from marts.fct_orders",
                ("now()/current_timestamp", "current_date"),
            ),
            ("select max(order_date) from marts.fct_orders", ()),
        ],
    )
    def test_clock_calls_are_reported_without_failing(self, guard: SqlGuard, sql: str, calls: tuple[str, ...]) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors
        assert result.clock_calls == calls

    @pytest.mark.parametrize("call", ["localtimestamp", "localtime", "current_time"])
    def test_other_clock_keywords_are_rejected(self, guard: SqlGuard, call: str) -> None:
        result = guard.check(f"select {call} from marts.fct_orders")

        assert result.errors == (f"[guard:function] function {call} is not allowed; {FUNCTION_HINT}",)

    def test_parse_error_is_a_rejection(self, guard: SqlGuard) -> None:
        result = guard.check("select from where (")

        assert not result.ok
        assert result.errors[0].startswith("[guard:parse] could not parse the query")
        assert result.tables == ()


class TestWholeRowReferences:
    @pytest.mark.parametrize(
        "sql",
        [
            "select c.email, c.customer_id from marts.dim_customers c",
            "select count(*) from marts.dim_customers",
            "select c.* from marts.dim_customers c",
            "select *, c.*, c.email from marts.dim_customers c",
            "select x.* from (select c.* from marts.dim_customers c) x",
            "with t as (select c.* from marts.dim_customers c) select t.* from t",
            "select customer_id from marts.dim_customers c where c.customer_id > 1",
            "with x as (select 1 as a) select a from x",
            "with revenue as (select sum(net_revenue) as revenue from marts.fct_orders) "
            "select r.revenue from revenue r",
            "select s.email from (select email from marts.dim_customers) s",
            "select c.customer_id, o.order_id from (marts.fct_orders o join marts.dim_customers c on true) limit 1",
            "select c.customer_id from marts.fct_orders o join "
            "(marts.dim_customers c join marts.fct_order_items d on true) on true",
            "select c.customer_id from ((marts.dim_customers c join marts.fct_order_items d on true))",
        ],
    )
    def test_ordinary_queries_still_pass(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors


class TestQualifiedColumns:
    """Postgres reads `t.f` as the call `f(t)` when `f` is not a column of `t`: `c.row_to_json` is the whole row."""

    # the guard never looks at the name here, so two are enough
    @pytest.mark.parametrize("function", ["row_to_json", "num_nulls"])
    @pytest.mark.parametrize(
        "template",
        [
            "select c.{f} from marts.dim_customers c limit 1",
            "select c.{f} from marts.dim_customers as c",
            "select dim_customers.{f} from marts.dim_customers",
            "select marts.dim_customers.{f} from marts.dim_customers",
            "select C.{f} from marts.dim_customers c",
            'select "c".{f} from marts.dim_customers c',
            'select "C".{f} from marts.dim_customers "C"',
            'select c."{f}" from marts.dim_customers c',
            "select upper(c.{f}::text) from marts.dim_customers c",
            "select c.email from marts.dim_customers c where c.{f} is not null",
            "select c.email from marts.dim_customers c order by c.{f}",
            "select o.order_id from marts.fct_orders o join marts.dim_customers c on c.{f} is not null",
            "with x as (select * from marts.dim_customers) select x.{f} from x",
            "with x as (select c.* from marts.dim_customers c) select x.{f} from x",
            "with x as (select email from marts.dim_customers) select x.{f} from x",
            "with x as (select * from marts.dim_customers), y as (select * from x) select y.{f} from y",
            "select s.{f} from (select * from marts.dim_customers) s",
            "select s.{f} from (select count(*), max(email) from marts.dim_customers) s",
            "select s.{f} from (select count(*) filter (where email is not null) from marts.dim_customers) s",
            "select s.{f} from (select email, first_name from marts.dim_customers) s",
            "select s.{f} from (select email from marts.dim_customers union select email from marts.dim_customers) s",
            "select l.{f} from marts.fct_orders o, lateral (select c.email from marts.dim_customers c) l",
            "select o.order_id from marts.fct_orders o where exists (select 1 from marts.dim_customers c where c.{f})",
            "select (select c.{f} from marts.dim_customers c limit 1) from marts.fct_orders",
            "select o.order_id from marts.fct_orders o where o.customer_id in (select c.{f} from marts.dim_customers c)"
            " limit 1",
        ],
    )
    def test_a_name_that_is_not_a_column_of_its_source_is_rejected(
        self, guard: SqlGuard, template: str, function: str
    ) -> None:
        result = guard.check(template.format(f=function))

        assert not result.ok
        assert any("is not a column of" in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        "sql",
        [
            # the qualifier is not a source in scope, or names more than a table
            "select x.email from marts.dim_customers c",
            "select c.email.row_to_json from marts.dim_customers c",
            "select sherpa.marts.dim_customers.email from marts.dim_customers",
            "select other.dim_customers.email from marts.dim_customers",
            "select marts.dim_customers.email from marts.dim_customers c",
            # a CTE that is not in FROM is not a source
            "with x as (select email from marts.dim_customers) select x.email from marts.dim_customers",
            # a quoted qualifier is case-sensitive, as in Postgres
            'select "C".email from marts.dim_customers c',
            # a derived table's column list renames its columns
            "select s.email from (select email from marts.dim_customers) s(e)",
            "select c.email from marts.dim_customers c(id, mail)",
            # field selection on a parenthesised value
            "select (c.email).row_to_json from marts.dim_customers c",
        ],
    )
    def test_unresolvable_qualified_columns_fail_closed(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert not result.ok, sql

    @pytest.mark.parametrize(
        "sql",
        [
            "select c.email, c.customer_id from marts.dim_customers c",
            "select C.Email from marts.dim_customers c",
            'select "c"."email" from marts.dim_customers c',
            "select dim_customers.email, marts.dim_customers.customer_id from marts.dim_customers",
            "select c.*, c.email from marts.dim_customers c",
            """
            select o.order_id, c.customer_segment, i.quantity
            from marts.fct_orders o
            join marts.dim_customers c on c.customer_id = o.customer_id
            left join marts.fct_order_items i on i.order_id = o.order_id
            where o.net_revenue > 0
            order by o.order_date desc
            """,
            """
            with monthly as (
                select o.customer_id, date_trunc('month', o.order_date) as month, sum(o.net_revenue) as revenue
                from marts.fct_orders o
                group by 1, 2
            ),
            ranked as (
                select m.*, rank() over (partition by m.month order by m.revenue desc) as rn from monthly m
            )
            select r.month, r.revenue, c.customer_segment
            from ranked r join marts.dim_customers c on c.customer_id = r.customer_id
            where r.rn <= 3
            """,
            "with x as (select * from marts.dim_customers) select x.email, x.customer_segment from x",
            "with x(id, mail) as (select customer_id, email from marts.dim_customers) select x.id, x.mail from x",
            "select s.e from (select email from marts.dim_customers) s(e)",
            "select s.total from (select sum(net_revenue) as total from marts.fct_orders) s",
            "select s.email from (select email from marts.dim_customers union select email from marts.dim_customers) s",
            "select v.a from (values (1), (2)) v(a)",
            # Postgres names an unaliased count/sum/avg/min/max column after the function
            "select t.count from (select count(*) from marts.fct_orders) t",
            "select t.sum, t.avg, t.min, t.max from ("
            "select sum(net_revenue), avg(net_revenue), min(order_date), MAX(order_date) from marts.fct_orders) t",
            "with x as (select count(distinct customer_id) from marts.fct_orders) select x.count from x",
            "select o.order_id, l.email from marts.fct_orders o, lateral ("
            "select c.email from marts.dim_customers c where c.customer_id = o.customer_id) l",
            "select o.order_id from marts.fct_orders o "
            "where exists (select 1 from marts.dim_customers c where c.customer_id = o.customer_id)",
            "select o.order_id, (select max(i.quantity) from marts.fct_order_items i where i.order_id = o.order_id) "
            "from marts.fct_orders o",
            # an inner alias shadows the outer one, as in Postgres
            "select c.order_id from marts.fct_orders c "
            "where exists (select 1 from marts.dim_customers c where c.email is not null)",
            "select count(*) over (partition by o.channel order by o.order_date) from marts.fct_orders o",
            # unqualified names are not the attribute form; Postgres reports an unknown one as a missing column
            "select row_to_json from marts.dim_customers c",
        ],
    )
    def test_ordinary_qualified_queries_pass(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors

    @pytest.mark.parametrize(
        "sql",
        [
            "select ab.email from marts.dim_customers as ab(a)",
            "select s.first_name from (select email, first_name from marts.dim_customers) s(e)",
        ],
    )
    def test_a_column_left_out_of_an_alias_list_gets_an_aliasing_hint(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert len(result.errors) == 1
        assert result.errors[0].endswith(
            "; the source's alias list (`s(a, b)`) renames its columns by position, so name only those, "
            "or drop the list and alias the columns in the select list (`email as e`)"
        ), result.errors

    def test_error_names_the_column_and_its_source(self, guard: SqlGuard) -> None:
        result = guard.check("select c.row_to_json from marts.dim_customers c")

        assert result.errors == (
            "[guard:column] column c.row_to_json is not a column of marts.dim_customers; "
            "use a column listed by describe_table",
        )

    @pytest.mark.parametrize("depth", [500, 2000])
    def test_a_chain_too_deep_to_resolve_is_rejected_not_raised(self, guard: SqlGuard, depth: int) -> None:
        ctes = ["c0 as (select * from marts.dim_customers)"] + [
            f"c{i} as (select * from c{i - 1})" for i in range(1, depth)
        ]
        sql = f"with {', '.join(ctes)} select c{depth - 1}.email from c{depth - 1}"

        result = guard.check(sql)

        assert result.errors == ("[guard:column] could not resolve the column references of the query; simplify it",)

    def test_a_mart_with_no_known_columns_resolves_nothing(self) -> None:
        guard = SqlGuard({"dim_customers": frozenset()})

        result = guard.check("select c.email from marts.dim_customers c")

        assert not result.ok


def _says(error: str, rule: str, message: str) -> bool:
    """The error is `[guard:<rule>] <message>`, then the end, a `;` hint or a `:` detail (not a longer word)."""
    rest = error.removeprefix(f"[guard:{rule}] ")

    return rest.startswith(message) and not rest[len(message) :][:1].isalnum()


def _call(name: str, arguments: int) -> str:
    return f"select {name}({', '.join(['1'] * arguments)}) from marts.fct_orders"
