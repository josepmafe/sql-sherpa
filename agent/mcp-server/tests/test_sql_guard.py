from __future__ import annotations

import re

import pytest
from sherpa_mcp.database import MAX_CELL_CHARS
from sherpa_mcp.sql_guard import ALLOWED_FUNCTIONS, MAX_PAD_LENGTH, SqlGuard

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


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard(MART_COLUMNS)


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

    def test_pad_limit_is_the_cell_cap(self) -> None:
        assert MAX_PAD_LENGTH == MAX_CELL_CHARS

    def test_repeat_stays_rejected(self, guard: SqlGuard) -> None:
        result = guard.check("select repeat('a', 5) from marts.fct_orders")

        assert not result.ok

    def test_allowed_functions_can_be_replaced(self) -> None:
        narrow = SqlGuard(MART_COLUMNS, allowed_functions=frozenset({"count"}))
        wide = SqlGuard(MART_COLUMNS, allowed_functions=ALLOWED_FUNCTIONS | {"md5"})

        assert narrow.check("select count(*) from marts.fct_orders").ok
        assert not narrow.check("select upper(channel) from marts.fct_orders").ok
        assert wide.check("select md5(channel) from marts.fct_orders").ok
        assert not SqlGuard(MART_COLUMNS).check("select md5(channel) from marts.fct_orders").ok

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

    @pytest.mark.parametrize(
        ("sql", "fragment"),
        [
            # one statement only
            ("select 1 from marts.fct_orders; drop table x", "exactly one statement, got 2"),
            ("select 1 from marts.fct_orders -- c\n; delete from marts.fct_orders", "exactly one statement, got 2"),
            ("select 'a\\' ; delete from marts.fct_orders; --' from marts.fct_orders", "exactly one statement"),
            ("", "exactly one statement, got 0"),
            ("-- only a comment", "exactly one statement, got 0"),
            # DML, DDL and session changes
            ("delete from marts.fct_orders", "must be a SELECT query, not DELETE"),
            ("update marts.fct_orders set net_revenue = 0", "must be a SELECT query, not UPDATE"),
            ("insert into marts.fct_orders values (1)", "must be a SELECT query, not INSERT"),
            ("merge into marts.fct_orders t using marts.dim_customers s on true when matched then delete", "MERGE"),
            ("create table x as select 1", "must be a SELECT query, not CREATE"),
            ("drop table marts.fct_orders", "must be a SELECT query, not DROP"),
            ("truncate marts.fct_orders", "must be a SELECT query"),
            ("set statement_timeout = 0", "must be a SELECT query, not SET"),
            ("reset all", "must be a SELECT query, not RESET"),
            ("alter role sherpa_agent set default_transaction_read_only = off", "must be a SELECT query, not ALTER"),
            ("set role postgres", "must be a SELECT query, not SET"),
            ("set session authorization postgres", "must be a SELECT query, not SET"),
            ("do $$ begin perform pg_sleep(10); end $$", "must be a SELECT query, not DO"),
            ("explain analyze delete from marts.fct_orders", "must be a SELECT query, not EXPLAIN"),
            ("prepare p as select 1", "must be a SELECT query, not PREPARE"),
            ("execute p", "must be a SELECT query, not EXECUTE"),
            ("listen x", "must be a SELECT query"),
            ("notify x", "must be a SELECT query"),
            ("call proc()", "must be a SELECT query, not CALL"),
            ("vacuum", "must be a SELECT query, not VACUUM"),
            ("lock table marts.fct_orders", "must be a SELECT query, not LOCK"),
            ("discard all", "must be a SELECT query"),
            ("table pg_roles", "must be a SELECT query"),
            ("values (1)", "must be a SELECT query"),
            ("copy marts.fct_orders to stdout", "must be a SELECT query, not COPY"),
            ("copy (select 1) to program 'id'", "must be a SELECT query, not COPY"),
            ("begin", "must be a SELECT query"),
            ("grant select on marts.fct_orders to public", "must be a SELECT query"),
            # writes hidden inside a query
            ("select * into x from marts.fct_orders", "SELECT ... INTO is not allowed"),
            ("select * from (select * into t from marts.fct_orders) s", "SELECT ... INTO is not allowed"),
            ("with d as (delete from marts.fct_orders returning *) select * from d", "DELETE is not allowed"),
            ("with u as (update marts.fct_orders set a = 1 returning *) select * from u", "UPDATE is not allowed"),
            (
                "with i as (insert into marts.fct_orders values (1) returning *) select * from i",
                "INSERT is not allowed",
            ),
            ("select * from marts.fct_orders for update", "locking clause FOR UPDATE/SHARE is not allowed"),
            ("select * from marts.fct_orders for share", "locking clause FOR UPDATE/SHARE is not allowed"),
            ("select * from (select * from marts.fct_orders for no key update) s", "locking clause"),
            # functions outside the allow-list
            ("select pg_sleep(10)", "function pg_sleep is not allowed"),
            ("select query_to_xml('select 1', true, true, '')", "function query_to_xml is not allowed"),
            ("select current_setting('x')", "function current_setting is not allowed"),
            ("select set_config('a', 'b', false)", "function set_config is not allowed"),
            ("select * from dblink('host=x', 'select 1') as t(a int)", "function dblink is not allowed"),
            ("select dblink_exec('host=x', 'drop table y')", "function dblink_exec is not allowed"),
            ("select lo_import('/etc/passwd')", "function lo_import is not allowed"),
            ("select pg_read_file('/etc/passwd')", "function pg_read_file is not allowed"),
            ("select * from pg_ls_dir('.')", "function pg_ls_dir is not allowed"),
            ("select * from pg_read_file('/etc/passwd') as t(x)", "function pg_read_file is not allowed"),
            ("select txid_current()", "function txid_current is not allowed"),
            ("select row_to_json(o) from marts.fct_orders o", "function row_to_json is not allowed"),
            ("select current_user", "is not allowed"),
            ("select version()", "is not allowed"),
            ('select "pg_sleep"(1)', "function pg_sleep is not allowed"),
            ("select 1 from marts.fct_orders where net_revenue > 0 and pg_sleep(1) is null", "function pg_sleep"),
            ("select 1 from marts.fct_orders order by (select pg_sleep(1))", "function pg_sleep is not allowed"),
            # casts to reg* types look names up in the system catalogs
            ("select 10::regrole::text from marts.fct_orders", "cast to type REGROLE is not allowed"),
            ("select cast('pg_authid' as regclass) from marts.fct_orders", "cast to type REGCLASS is not allowed"),
            ("select 'x'::regnamespace", "cast to type REGNAMESPACE is not allowed"),
            ("select 1::pg_catalog.regclass", "cast to type pg_catalog.regclass is not allowed"),
            # table functions in FROM
            ("select * from generate_series(1, 3)", "is not allowed"),
            ("select * from unnest(array[1, 2])", "function unnest is not allowed"),
            ("select * from rows from (generate_series(1, 2))", "is not allowed"),
            ("select * from marts.fct_orders o cross join lateral generate_series(1, 2) g", "is not allowed"),
            # schema-qualified functions, even allowed ones
            ("select pg_catalog.pg_sleep(1)", "function pg_catalog.pg_sleep is not allowed"),
            ("select pg_catalog.lower('a') from marts.fct_orders", "function pg_catalog.lower is not allowed"),
            ("select marts.lower('a') from marts.fct_orders", "function marts.lower is not allowed"),
            # tables outside the visible marts
            ("select * from pg_catalog.pg_roles", "table pg_catalog.pg_roles is outside marts"),
            ("select * from information_schema.tables", "table information_schema.tables is outside marts"),
            ("select * from fct_orders", "table fct_orders is not schema-qualified; qualify it as marts.fct_orders"),
            ("select * from pg_roles", "qualify it as marts.pg_roles"),
            ('select * from "marts.fct_orders"', "is not schema-qualified"),
            ("select * from staging.stg_shop__orders", "table staging.stg_shop__orders is outside marts"),
            # the dev target's schemas hold the same marts under another name
            ("select * from dev_marts.dim_customers", "table dev_marts.dim_customers is outside marts"),
            ("select * from marts_dev.dim_customers", "table marts_dev.dim_customers is outside marts"),
            ('select * from "dev_marts"."fct_orders"', "table dev_marts.fct_orders is outside marts"),
            ("select * from marts.dim_customers_v2", "table marts.dim_customers_v2 is not a visible mart"),
            ('select * from marts."fct_orders "', "is not a visible mart"),
            ("select * from sherpa.marts.fct_orders", "table sherpa.marts.fct_orders is not allowed"),
            ("select * from a.b.c.d", "is not allowed"),
            ("select * from marts.fct_orders, pg_catalog.pg_user", "table pg_catalog.pg_user is outside marts"),
            ("select * from marts.fct_orders join pg_roles on true", "qualify it as marts.pg_roles"),
            ("select * from marts.fct_orders natural join pg_catalog.pg_class", "pg_catalog.pg_class is outside marts"),
            ("select * from marts.fct_orders o, lateral (select * from pg_catalog.pg_roles) r", "pg_catalog.pg_roles"),
            ("select (select rolpassword from pg_authid limit 1) from marts.fct_orders", "marts.pg_authid"),
            (
                "select 1 from marts.fct_orders where exists (select 1 from pg_catalog.pg_shadow)",
                "pg_catalog.pg_shadow",
            ),
            ("select 1 from marts.fct_orders union select 2 from pg_roles", "qualify it as marts.pg_roles"),
            ("select * from marts.fct_orders tablesample bernoulli (10), pg_catalog.pg_proc", "pg_catalog.pg_proc"),
            ("select a from (select a from (select a from pg_catalog.pg_stats) x) y", "pg_catalog.pg_stats"),
            ("select * from (values (1)) v(a) join pg_catalog.pg_roles r on true", "pg_catalog.pg_roles"),
            # CTE names only cover references Postgres resolves to the CTE
            ('with "PG_ROLES" as (select 1) select * from pg_roles', "qualify it as marts.pg_roles"),
            ("with pg_roles as (select * from pg_roles) select * from pg_roles", "qualify it as marts.pg_roles"),
            ("with a as (select * from pg_roles), pg_roles as (select 1) select * from a", "marts.pg_roles"),
            ("select * from (with pg_roles as (select 1) select * from pg_roles) s, pg_roles", "marts.pg_roles"),
            ("select 1 where 1 in (with x as (select 1) select * from x) and 1 in (select * from x)", "marts.x"),
            ("(with x as (select 1) select * from x) union select * from x", "qualify it as marts.x"),
            # parser differentials and malformed input
            ("select 1 from marts.fct_orders # 1", "could not parse"),
            ('select * from U&"pg_\\0072oles"', "could not parse"),
            ("select xmlparse(document '<a/>')", "could not parse"),
            ("select " + "(" * 2000 + "1" + ")" * 2000, "could not parse"),
        ],
    )
    def test_rejects_hostile_inputs(self, guard: SqlGuard, sql: str, fragment: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any(fragment in error for error in result.errors), result.errors

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

    @pytest.mark.parametrize(
        ("sql", "rule"),
        [
            ("select 1 from", "parse"),
            ("select 1 from marts.fct_orders; select 2 from marts.fct_orders", "statement_count"),
            ("delete from marts.fct_orders", "not_select"),
            ("with d as (delete from marts.fct_orders returning *) select * from d", "write"),
            ("select * into x from marts.fct_orders", "select_into"),
            ("select * from marts.fct_orders for update", "locking"),
            ("select 1::regclass from marts.fct_orders", "cast"),
            ("select pg_sleep(1)", "function"),
            ("select (c.email).x from marts.dim_customers c", "field"),
            ("select c from marts.dim_customers c", "whole_row"),
            ("select c.nope from marts.dim_customers c", "column"),
            ("select * from pg_roles", "table"),
        ],
    )
    def test_every_error_starts_with_its_rule_code(self, guard: SqlGuard, sql: str, rule: str) -> None:
        result = guard.check(sql)

        assert result.errors
        assert result.errors[0].startswith(f"[guard:{rule}] "), result.errors
        assert all(re.match(r"\[guard:\w+\] ", error) for error in result.errors), result.errors

    @pytest.mark.parametrize(
        ("sql", "hint"),
        [
            ("select 1 from marts.fct_orders where order_id = any(array[1, 2])", "use IN (...)"),
            ("select 1 from marts.fct_orders where channel ~ 'web'", "use LIKE or ILIKE"),
            ("select 1 from marts.fct_orders where channel ~* 'web'", "use LIKE or ILIKE"),
            ("(select 1 from marts.fct_orders)", "remove the outer parentheses"),
            ("select * from generate_series(1, 3)", "use marts.dim_date for a series of dates"),
            ("select to_timestamp(0) from marts.fct_orders", "use to_char, make_date or date_trunc"),
            ("select json_agg(order_id) from marts.fct_orders", "aggregate with string_agg, or select the columns"),
            ("select jsonb_build_object('a', 1) from marts.fct_orders", "aggregate with string_agg"),
            ("select row_to_json(o) from marts.fct_orders o", "aggregate with string_agg"),
            ("select grouping(channel) from marts.fct_orders group by channel", "one query per grouping level"),
            ("select random() from marts.fct_orders", FUNCTION_HINT),
            ("select channel @> 'x' from marts.fct_orders", "use comparison operators, LIKE/ILIKE or IN (...)"),
        ],
    )
    def test_rejection_carries_a_remedy_hint(self, guard: SqlGuard, sql: str, hint: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any(error.endswith(hint) or f"; {hint}" in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        ("sql", "calls"),
        [
            ("select now() - interval '1 day' from marts.fct_orders", ("current_timestamp",)),
            ("select 1 from marts.fct_orders where order_date >= current_date - 30", ("current_date",)),
            (
                "select current_timestamp, current_date, now() from marts.fct_orders",
                ("current_timestamp", "current_date"),
            ),
            ("select max(order_date) from marts.fct_orders", ()),
        ],
    )
    def test_clock_calls_are_reported_without_failing(self, guard: SqlGuard, sql: str, calls: tuple[str, ...]) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors
        assert result.clock_calls == calls

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("select json_agg(order_id) from marts.fct_orders", "function json_agg is not allowed"),
            ("select to_timestamp(0) from marts.fct_orders", "function to_timestamp is not allowed"),
            ("select random() from marts.fct_orders", "function random is not allowed"),
            ("select * from generate_series(1, 3)", "function generate_series is not allowed"),
            ("select * from public.generate_series(1, 3)", "function generate_series is not allowed"),
            ("select json_table(a) from marts.fct_orders", "function json_table is not allowed"),
            ("select array_length(a, 1) from marts.fct_orders", "function array_length is not allowed"),
            ("select current_user from marts.fct_orders", "function current_user is not allowed"),
            ("select version()", "function version is not allowed"),
            ("select localtimestamp from marts.fct_orders", "function localtimestamp is not allowed"),
            ("select grouping(a) from marts.fct_orders group by a", "function grouping is not allowed"),
            ("select a ->> 'k' from marts.fct_orders", "function or operator `a ->> 'k'` is not allowed"),
            ("select a @> b from marts.fct_orders", "function or operator `a @> b` is not allowed"),
            ("select 1 from marts.fct_orders where a ~ 'x'", "function or operator `a ~ 'x'` is not allowed"),
            ("select 1 from marts.fct_orders where a = any(array[1, 2])", "function or operator `ARRAY[1, 2]`"),
            (
                "select a ->> '" + "k" * 80 + "' from marts.fct_orders",
                "function or operator `" + ("a ->> '" + "k" * 80)[:60] + "...` is not allowed",
            ),
        ],
    )
    def test_rejected_function_is_named_as_written_in_postgres(self, guard: SqlGuard, sql: str, error: str) -> None:
        result = guard.check(sql)

        assert any(error in item for item in result.errors), result.errors
        assert SQLGLOT_NODE_NAME.search(" ".join(result.errors)) is None

    def test_parse_error_is_a_rejection(self, guard: SqlGuard) -> None:
        result = guard.check("select from where (")

        assert not result.ok
        assert result.errors[0].startswith("[guard:parse] could not parse the query")
        assert result.tables == ()


class TestWholeRowReferences:
    @pytest.mark.parametrize(
        "sql",
        [
            "select c from marts.dim_customers c",
            "select c from marts.dim_customers as c",
            "select dim_customers from marts.dim_customers",
            "select C from marts.dim_customers c",
            "select o.order_id, c from marts.fct_orders o join marts.dim_customers c on c.customer_id = o.customer_id",
            "with x as (select 1 as a) select x from x",
            "select s from (select email from marts.dim_customers) s",
            "select * from marts.fct_orders where exists (select c from marts.dim_customers c)",
        ],
    )
    def test_rejects_unqualified_table_alias_name_or_cte(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any("whole-row reference" in error and "select named columns" in error for error in result.errors)

    @pytest.mark.parametrize(
        ("sql", "fragment"),
        [
            ("select to_json(c) from marts.dim_customers c", "function to_json is not allowed"),
            ("select row_to_json(c) from marts.dim_customers c", "function row_to_json is not allowed"),
            ("select row(c.email) from marts.dim_customers c", "function row is not allowed"),
            ("select c::text from marts.dim_customers c", "whole-row reference `c`"),
        ],
    )
    def test_record_conversions_stay_rejected(self, guard: SqlGuard, sql: str, fragment: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any(fragment in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        ("sql", "star"),
        [
            ("select c.*::text from marts.dim_customers c limit 1", "c.*"),
            ("select concat(c.*) from marts.dim_customers c limit 1", "c.*"),
            ("select (c.*)::text as x from marts.dim_customers c limit 1", "c.*"),
            ("select array_agg(c.*) from marts.dim_customers c", "c.*"),
            ("select coalesce(c.*) from marts.dim_customers c", "c.*"),
            ("select c.* || 'x' from marts.dim_customers c", "c.*"),
            ("select c.* is null from marts.dim_customers c", "c.*"),
            ("select count(c.*) from marts.dim_customers c", "c.*"),
            # Postgres expands `c.* as x` and ignores the alias; the guard fails closed on it anyway.
            ("select c.* as x from marts.dim_customers c", "c.*"),
            ("select c.email from marts.dim_customers c order by c.*", "c.*"),
            ("select c.email from marts.dim_customers c where c.* is not null", "c.*"),
            ("select c.email from marts.dim_customers c where (c.*) = (c.*)", "c.*"),
            ("select count(*) from marts.dim_customers c group by c.*", "c.*"),
            ("select count(*) from marts.dim_customers c group by 1 having count(c.*) > 0", "c.*"),
            ("select distinct on (c.*) c.email from marts.dim_customers c", "c.*"),
            ("select count(*) over (partition by c.*) from marts.dim_customers c", "c.*"),
            ("select x from (select c.*::text as x from marts.dim_customers c) s", "c.*"),
            ("with t as (select c.*::text as x from marts.dim_customers c) select x from t", "c.*"),
            ("select c.* from marts.dim_customers c union select c.*::text from marts.dim_customers c", "c.*"),
            ('select "C".*::text from marts.dim_customers "C"', '"C".*'),
            ("select dim_customers.*::text from marts.dim_customers", "dim_customers.*"),
            ("select marts.dim_customers.*::text from marts.dim_customers", "marts.dim_customers.*"),
        ],
    )
    def test_rejects_qualified_star_inside_an_expression(self, guard: SqlGuard, sql: str, star: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert (
            f"[guard:whole_row] whole-row reference `{star}` inside an expression is not allowed; select named columns"
            in (result.errors)
        ), result.errors

    @pytest.mark.parametrize(
        ("sql", "fragment"),
        [
            ("select row_to_json(c.*) from marts.dim_customers c", "function row_to_json is not allowed"),
            ("select to_jsonb(c.*) from marts.dim_customers c", "function to_jsonb is not allowed"),
            ("select (c).* from marts.dim_customers c", "whole-row reference `c`"),
            ("select (c).email from marts.dim_customers c", "whole-row reference `c`"),
        ],
    )
    def test_record_star_conversions_are_rejected(self, guard: SqlGuard, sql: str, fragment: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any(fragment in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        "sql",
        [
            'select "C" from marts.dim_customers "C"',
            'select "Foo" from marts.dim_customers as "Foo"',
            'select "dim_customers" from marts.dim_customers',
            'select "DIM_CUSTOMERS" from marts.dim_customers',
            'with "X" as (select 1) select "X" from "X"',
            # Postgres folds unquoted Foo to foo, a plain unknown column; the guard fails closed and rejects it too.
            'select Foo from marts.dim_customers as "Foo"',
            'select "C" from marts.dim_customers c',
        ],
    )
    def test_quoted_names_are_matched_case_insensitively(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert not result.ok
        assert any("whole-row reference" in error for error in result.errors), result.errors

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
            "select s.email from (select email from marts.dim_customers) s",
        ],
    )
    def test_ordinary_queries_still_pass(self, guard: SqlGuard, sql: str) -> None:
        result = guard.check(sql)

        assert result.ok, result.errors


class TestQualifiedColumns:
    """Postgres reads `t.f` as the call `f(t)` when `f` is not a column of `t`: `c.row_to_json` is the whole row."""

    @pytest.mark.parametrize(
        "function", ["row_to_json", "to_json", "to_jsonb", "record_out", "pg_column_size", "num_nulls", "textin"]
    )
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

    def test_error_names_the_column_and_its_source(self, guard: SqlGuard) -> None:
        result = guard.check("select c.row_to_json from marts.dim_customers c")

        assert result.errors == (
            "[guard:column] column c.row_to_json is not a column of marts.dim_customers; "
            "use a column listed by describe_table",
        )

    def test_a_mart_with_no_known_columns_resolves_nothing(self) -> None:
        guard = SqlGuard({"dim_customers": frozenset()})

        result = guard.check("select c.email from marts.dim_customers c")

        assert not result.ok
