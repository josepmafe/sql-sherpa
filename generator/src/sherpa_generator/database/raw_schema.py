from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database

SCHEMAS = ("raw_shop", "raw_events", "raw_marketing", "raw_crm", "raw_meta")

# Raw tables are deliberately loose: enums are text, foreign keys are plain bigint without constraints, and the
# event tables have no key at all (Spec A §4: duplicates must be possible, dbt dedupes).
# Column order matches the record classes in sherpa_generator.records; a test keeps them in sync.
_EVENT_COLUMNS = """
    event_id text,
    anonymous_id text,
    user_id text,
    event_type text,
    occurred_at text,
    received_at timestamptz,
    context jsonb,
    properties jsonb
"""

TABLE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS raw_shop.customers (
        id bigint PRIMARY KEY,
        email text,
        first_name text,
        last_name text,
        country_code text,
        marketing_opt_in boolean,
        is_test_account boolean,
        created_at timestamptz,
        updated_at timestamptz,
        deleted_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.addresses (
        id bigint PRIMARY KEY,
        customer_id bigint,
        type text,
        country_code text,
        city text,
        postal_code text,
        is_default boolean
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.categories (
        id bigint PRIMARY KEY,
        parent_id bigint,
        name text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.products (
        id bigint PRIMARY KEY,
        category_id bigint,
        name text,
        brand text,
        is_active boolean,
        created_at timestamptz,
        updated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.product_variants (
        id bigint PRIMARY KEY,
        product_id bigint,
        sku text,
        size text,
        color text,
        cost_price numeric(12,2),
        list_price numeric(12,2),
        created_at timestamptz,
        updated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.orders (
        id bigint PRIMARY KEY,
        order_number text,
        customer_id bigint,
        shipping_address_id bigint,
        status text,
        subtotal numeric(12,2),
        discount_total numeric(12,2),
        shipping_amount numeric(12,2),
        tax_amount numeric(12,2),
        order_total numeric(12,2),
        sales_channel text,
        placed_at timestamptz,
        updated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.order_items (
        id bigint PRIMARY KEY,
        order_id bigint,
        variant_id bigint,
        quantity integer,
        unit_price numeric(12,2)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.order_status_history (
        id bigint PRIMARY KEY,
        order_id bigint,
        status text,
        changed_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.payments (
        id bigint PRIMARY KEY,
        order_id bigint,
        method text,
        amount numeric(12,2),
        status text,
        created_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.shipments (
        id bigint PRIMARY KEY,
        order_id bigint,
        carrier text,
        shipping_cost numeric(12,2),
        shipped_at timestamptz,
        delivered_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.promotions (
        id bigint PRIMARY KEY,
        name text,
        type text,
        value numeric(12,2),
        min_order_value numeric(12,2),
        campaign_id bigint,
        starts_at timestamptz,
        ends_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.coupon_codes (
        id bigint PRIMARY KEY,
        promotion_id bigint,
        code text,
        max_redemptions integer,
        is_single_use boolean
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.order_discounts (
        id bigint PRIMARY KEY,
        order_id bigint,
        promotion_id bigint,
        coupon_code_id bigint,
        amount numeric(12,2)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.returns (
        id bigint PRIMARY KEY,
        order_id bigint,
        reason text,
        status text,
        requested_at timestamptz,
        updated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.return_items (
        id bigint PRIMARY KEY,
        return_id bigint,
        order_item_id bigint,
        quantity integer,
        is_restockable boolean
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_shop.refunds (
        id bigint PRIMARY KEY,
        order_id bigint,
        return_id bigint,
        payment_id bigint,
        amount numeric(12,2),
        reason text,
        refunded_at timestamptz
    )
    """,
    f"CREATE TABLE IF NOT EXISTS raw_events.events ({_EVENT_COLUMNS})",
    f"CREATE TABLE IF NOT EXISTS raw_meta.pending_events ({_EVENT_COLUMNS})",
    """
    CREATE TABLE IF NOT EXISTS raw_marketing.campaigns (
        id bigint PRIMARY KEY,
        name text,
        utm_campaign text,
        channel text,
        starts_at timestamptz,
        ends_at timestamptz,
        budget numeric(12,2)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_marketing.ad_spend_daily (
        spend_date date,
        campaign_id bigint,
        spend numeric(12,2),
        impressions integer,
        clicks integer,
        PRIMARY KEY (spend_date, campaign_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_crm.leads (
        id bigint PRIMARY KEY,
        email text,
        source text,
        utm_campaign text,
        lifecycle_stage text,
        created_at timestamptz,
        updated_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_crm.email_sends (
        id bigint PRIMARY KEY,
        lead_id bigint,
        campaign_id bigint,
        sent_at timestamptz,
        opened_at timestamptz,
        clicked_at timestamptz
    )
    """,
    # Spec A §5.3
    """
    CREATE TABLE IF NOT EXISTS raw_meta.simulation_state (
        id int PRIMARY KEY CHECK (id = 1),
        seed bigint,
        scale numeric,
        start_date date,
        last_simulated_date date,
        next_ids jsonb,
        updated_at timestamptz
    )
    """,
)


class RawSchema:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self) -> None:
        with self._db.transaction() as conn:
            for schema in SCHEMAS:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

            for ddl in TABLE_DDL:
                conn.execute(ddl)  # type: ignore[arg-type]  # static SQL strings only

    def drop(self) -> None:
        with self._db.transaction() as conn:
            for schema in SCHEMAS:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    def tables(self) -> dict[str, tuple[str, ...]]:
        """Schema-qualified table name -> column names in table order, read back from the database."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT table_schema, table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = ANY(%s) ORDER BY table_schema, table_name, ordinal_position",
                [list(SCHEMAS)],
            ).fetchall()

        columns: dict[str, list[str]] = {}
        for schema, table, column in rows:
            columns.setdefault(f"{schema}.{table}", []).append(column)
        return {table: tuple(cols) for table, cols in columns.items()}
