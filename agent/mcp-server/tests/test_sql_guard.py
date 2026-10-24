from __future__ import annotations

import pytest
from sherpa_mcp.sql_guard import SqlGuard

VISIBLE_MARTS = frozenset({"fct_orders", "dim_customers", "fct_order_items"})


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard(VISIBLE_MARTS)


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

    def test_recursive_cte_may_reference_itself(self, guard: SqlGuard) -> None:
        sql = "with recursive n as (select 1 as i union all select i + 1 from n where i < 5) select * from n"

        result = guard.check(sql)

        assert result.ok

    def test_identifier_case_and_quotes(self, guard: SqlGuard) -> None:
        quoted = guard.check('select * from "marts"."fct_orders"')
        mixed_case = guard.check("SELECT * FROM MARTS.Fct_Orders")
        other_schema = guard.check('select * from "staging"."fct_orders"')

        assert quoted.ok
        assert mixed_case.ok
        assert quoted.tables == mixed_case.tables == ("marts.fct_orders",)
        assert not other_schema.ok
        assert "table staging.fct_orders is outside marts" in other_schema.errors

    def test_quoted_identifiers_keep_their_case(self, guard: SqlGuard) -> None:
        # Postgres folds unquoted names to lower case but keeps quoted ones verbatim, so these name other relations.
        upper_table = guard.check('select * from marts."FCT_ORDERS"')
        upper_schema = guard.check('select * from "MARTS".fct_orders')

        assert upper_table.errors == ("table marts.FCT_ORDERS is not a visible mart",)
        assert upper_schema.errors == ("table MARTS.fct_orders is outside marts",)

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
            "select * from marts.fct_orders o, lateral (select * from marts.dim_customers c where c.id = o.id) c",
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
            "function pg_sleep is not allowed",
            "table staging.stg_shop__orders is outside marts",
        )

    def test_repeated_violation_is_reported_once(self, guard: SqlGuard) -> None:
        result = guard.check("select pg_sleep(1), pg_sleep(2) from pg_catalog.pg_roles a, pg_catalog.pg_roles b")

        assert result.errors == (
            "function pg_sleep is not allowed",
            "table pg_catalog.pg_roles is outside marts",
        )

    def test_parse_error_is_a_rejection(self, guard: SqlGuard) -> None:
        result = guard.check("select from where (")

        assert not result.ok
        assert result.errors[0].startswith("could not parse the query")
        assert result.tables == ()
