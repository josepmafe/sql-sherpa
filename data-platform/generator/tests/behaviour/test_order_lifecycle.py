from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

import pytest
from helpers import MONTH_END, SIMULATION_SEED, make_customer
from sherpa_generator.behaviour.catalog_setup import CatalogBuilder
from sherpa_generator.behaviour.order_lifecycle import (
    PAID_CANCELLATION_PURPOSE,
    OrderLifecycleSimulator,
    OrderPricing,
)
from sherpa_generator.behaviour.visitor_traffic import EVENT_GAP_SECONDS, MAX_SESSION_GAPS, PurchaseIntent
from sherpa_generator.records import (
    Address,
    AddressType,
    CouponCode,
    DeviceType,
    EventContext,
    EventProperties,
    EventType,
    Order,
    OrderDiscount,
    OrderItem,
    OrderStatus,
    OrderStatusChange,
    Payment,
    PaymentMethod,
    PaymentStatus,
    PendingEvent,
    ProductVariant,
    Promotion,
    PromotionType,
    Refund,
    RefundReason,
    SalesChannel,
    Shipment,
    TrackedEvent,
)
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules
from sherpa_generator.simulation.store_snapshot import StoreSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    import psycopg
    from sherpa_generator.database.connection import Database

DAY = date(2025, 3, 12)
DAY_START = datetime(2025, 3, 12, tzinfo=UTC)
END_OF_RUN = datetime(MONTH_END.year, MONTH_END.month, MONTH_END.day, tzinfo=UTC)
CENT = Decimal("0.01")
MAX_SESSION_SECONDS = EVENT_GAP_SECONDS[1] * MAX_SESSION_GAPS  # the longest a session runs, add_to_cart to purchase
LINE_ORDERS = 400  # enough web orders to see every line count of LINE_COUNT_WEIGHTS
VALID_STATUS_PATHS = {
    ("placed",),
    ("placed", "cancelled"),
    ("placed", "paid"),
    ("placed", "paid", "cancelled"),
    ("placed", "paid", "shipped"),
    ("placed", "paid", "shipped", "delivered"),
}


class TestOrderPricing:
    def test_order_total_formula_examples(self) -> None:
        pricing = OrderPricing()

        discounted = pricing.order_total(Decimal("100.00"), Decimal("10.00"), free_shipping=False)
        below_threshold = pricing.order_total(Decimal("50.00"), Decimal("0.00"), free_shipping=False)
        free_shipping = pricing.order_total(Decimal("50.00"), Decimal("0.00"), free_shipping=True)

        assert discounted == Decimal("108.00")  # 100 - 10 + 0 shipping + 18 tax
        assert below_threshold == Decimal("64.95")  # 50 + 4.95 + 10
        assert free_shipping == Decimal("60.00")

    def test_shipping_free_at_exactly_75_after_discount(self) -> None:
        pricing = OrderPricing()

        assert pricing.shipping_amount(Decimal("74.99"), Decimal("0.00"), free_shipping=False) == Decimal("4.95")
        assert pricing.shipping_amount(Decimal("75.00"), Decimal("0.00"), free_shipping=False) == Decimal("0.00")
        assert pricing.shipping_amount(Decimal("85.00"), Decimal("10.01"), free_shipping=False) == Decimal("4.95")
        assert pricing.shipping_amount(Decimal("85.00"), Decimal("10.00"), free_shipping=False) == Decimal("0.00")
        assert pricing.shipping_amount(Decimal("10.00"), Decimal("0.00"), free_shipping=True) == Decimal("0.00")

    def test_tax_rounds_half_up(self) -> None:
        # Tax on whole cents always ends in an even tenth of a cent (2c/1000), so a .5 tie cannot occur here;
        # these cases pin rounding up from .8 and down from .4.
        pricing = OrderPricing()

        assert pricing.tax_amount(Decimal("12.34"), Decimal("0.00")) == Decimal("2.47")  # 2.468
        assert pricing.tax_amount(Decimal("12.32"), Decimal("0.00")) == Decimal("2.46")  # 2.464
        assert pricing.tax_amount(Decimal("100.00"), Decimal("10.00")) == Decimal("18.00")
        assert pricing.tax_amount(Decimal("10.00"), Decimal("10.00")) == Decimal("0.00")

    def test_percent_discount_rounds_half_up(self) -> None:
        pricing = OrderPricing()

        discount = pricing.discount_amount(_promotion(PromotionType.PERCENT, "15"), Decimal("12.30"))

        assert discount == Decimal("1.85")  # 1.845; half-even would give 1.84

    def test_fixed_discount_never_exceeds_subtotal(self) -> None:
        pricing = OrderPricing()
        promotion = _promotion(PromotionType.FIXED, "10")

        assert pricing.discount_amount(promotion, Decimal("9.99")) == Decimal("9.99")
        assert pricing.discount_amount(promotion, Decimal("10.00")) == Decimal("10.00")
        assert pricing.discount_amount(promotion, Decimal("10.01")) == Decimal("10.00")

    def test_free_shipping_discount_is_zero(self) -> None:
        discount = OrderPricing().discount_amount(_promotion(PromotionType.FREE_SHIPPING, "0"), Decimal("50.00"))

        assert discount == Decimal("0.00")

    def test_subtotal_multiplies_quantity_by_unit_price(self) -> None:
        items = [_item(1, 2, "19.99"), _item(2, 1, "5.01"), _item(3, 3, "0.10")]

        assert OrderPricing().subtotal(items) == Decimal("45.29")
        assert OrderPricing().subtotal([]) == Decimal("0.00")

    def test_allocate_discount_sums_exactly_and_remainder_goes_to_largest_line(self) -> None:
        lines = [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

        allocated = OrderPricing().allocate_discount(lines, Decimal("10.00"))

        assert allocated == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]

    def test_allocate_discount_takes_rounding_overshoot_from_largest_line(self) -> None:
        lines = [Decimal("1.00"), Decimal("1.00"), Decimal("1.01")]

        allocated = OrderPricing().allocate_discount(lines, Decimal("0.05"))

        assert allocated == [Decimal("0.02"), Decimal("0.02"), Decimal("0.01")]  # each 0.0166.. rounds up to 0.02

    def test_allocate_discount_is_pro_rata(self) -> None:
        lines = [Decimal("10.00"), Decimal("30.00"), Decimal("60.00")]

        allocated = OrderPricing().allocate_discount(lines, Decimal("20.00"))

        assert allocated == [Decimal("2.00"), Decimal("6.00"), Decimal("12.00")]

    def test_allocate_zero_discount_and_single_line(self) -> None:
        pricing = OrderPricing()

        assert pricing.allocate_discount([Decimal("10.00"), Decimal("20.00")], Decimal("0.00")) == [
            Decimal("0.00"),
            Decimal("0.00"),
        ]
        assert pricing.allocate_discount([Decimal("19.99")], Decimal("7.77")) == [Decimal("7.77")]
        assert pricing.allocate_discount([], Decimal("0.00")) == []


class TestOrderLifecycleSimulator:
    @pytest.mark.integration
    def test_order_totals_are_consistent_with_items_and_discounts(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _ORDER_TOTALS)

        assert rows
        for number, subtotal, discount, shipping, tax, total, items_sum, discounts_sum, lines, free in rows:
            assert number.startswith("SS-")
            assert len(number) == len("SS-000000")
            assert 1 <= lines <= 4
            assert subtotal == items_sum
            assert discount == discounts_sum
            assert Decimal(0) <= discount <= subtotal
            assert tax == ((subtotal - discount) * Decimal("0.20")).quantize(CENT)
            assert shipping == (Decimal("0.00") if free or subtotal - discount >= 75 else Decimal("4.95"))
            assert total == subtotal - discount + shipping + tax

    @pytest.mark.integration
    def test_captured_payments_equal_total_for_paid_orders(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _PAYMENTS_BY_ORDER)

        paid = [row for row in rows if row[1]]
        assert paid
        assert all(captured == total for _, _, total, captured, _, _ in paid)
        assert all(captured == 0 for _, was_paid, _, captured, _, _ in rows if not was_paid)
        assert any(captured_rows == 2 for *_, captured_rows, _ in paid)  # R7 split payments
        assert any(failed_rows > 0 for *_, failed_rows in paid)  # R7 failed attempts

    @pytest.mark.integration
    def test_status_history_is_monotonic_and_matches_current_status(self, month: Database) -> None:
        with month.connect() as conn:
            orders = {
                row[0]: row[1:] for row in _rows(conn, "SELECT id, status, placed_at, updated_at FROM raw_shop.orders")
            }
            history = _rows(conn, "SELECT order_id, status, changed_at FROM raw_shop.order_status_history ORDER BY id")

        by_order: dict[int, list[tuple[str, datetime]]] = defaultdict(list)
        for order_id, status, changed_at in history:
            by_order[order_id].append((status, changed_at))

        assert set(by_order) == set(orders)
        assert {path[-1][0] for path in by_order.values()} >= {"paid", "shipped", "delivered", "cancelled"}
        for order_id, (status, placed_at, updated_at) in orders.items():
            changes = by_order[order_id]
            times = [changed_at for _, changed_at in changes]
            assert tuple(s for s, _ in changes) in VALID_STATUS_PATHS
            assert changes[-1][0] == status
            assert times == sorted(times)
            assert times[0] == placed_at
            assert times[-1] == updated_at
            assert times[-1] < END_OF_RUN

    @pytest.mark.integration
    def test_discount_share(self, month: Database) -> None:
        with month.connect() as conn:
            [(orders, discounted)] = _rows(
                conn,
                "SELECT count(*), count(*) FILTER (WHERE id IN (SELECT order_id FROM raw_shop.order_discounts))"
                " FROM raw_shop.orders",
            )

        assert 0.18 <= discounted / orders <= 0.32

    @pytest.mark.integration
    def test_r8_orders_stack_a_coupon_of_another_promotion(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _STACKED_DISCOUNTS)

        assert rows
        for subtotal, discount_total, amounts, promotion_ids, coupon_ids, coupon_promotion_ids in rows:
            assert len(amounts) == 2
            assert promotion_ids[0] != promotion_ids[1]
            assert coupon_ids[0] is None
            assert coupon_ids[1] is not None
            assert coupon_promotion_ids[1] == promotion_ids[1]
            assert discount_total == sum(amounts)
            assert discount_total <= subtotal

    @pytest.mark.integration
    def test_app_share(self, month: Database) -> None:
        with month.connect() as conn:
            [(orders, app)] = _rows(
                conn, "SELECT count(*), count(*) FILTER (WHERE sales_channel = 'app') FROM raw_shop.orders"
            )

        assert 0.28 <= app / orders <= 0.42

    @pytest.mark.integration
    def test_every_web_order_has_exactly_one_purchase_event(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _PURCHASE_EVENTS_BY_ORDER)
            [(untagged,)] = _rows(conn, f"SELECT count(*) FROM ({_PURCHASE_EVENTS}) e WHERE order_id IS NULL")

        assert any(channel == "app" for _, channel, _ in rows)
        assert all(events == (1 if channel == "web" else 0) for _, channel, events in rows)
        assert untagged == 0

    @pytest.mark.integration
    def test_web_order_matches_its_purchase_event_and_leads_with_the_carted_variant(self, month: Database) -> None:
        with month.connect() as conn:
            rows = conn.execute(_WEB_ORDER_PURCHASES, [MAX_SESSION_SECONDS]).fetchall()
            [(web_orders,)] = _rows(conn, "SELECT count(*) FROM raw_shop.orders WHERE sales_channel = 'web'")

        assert web_orders > 0
        assert len(rows) == web_orders
        for subtotal, cart_value, item_variants, carted_variants in rows:
            assert cart_value == subtotal
            assert item_variants[0] in carted_variants

    @pytest.mark.integration
    def test_never_paid_orders_are_cancelled_next_day_without_refund(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _NEVER_PAID_ORDERS)

        cancelled = [row for row in rows if row[1] == "cancelled"]
        assert cancelled
        for _, status, placed_at, cancelled_at, payment_statuses, refunds in rows:
            assert payment_statuses == ["authorized"]
            assert refunds == 0
            if status == "placed":
                assert placed_at >= END_OF_RUN - timedelta(days=1)  # placed on the last day, cancelled tomorrow
            else:
                assert cancelled_at == placed_at + timedelta(days=1)

    @pytest.mark.integration
    def test_paid_cancellations_refund_the_captured_amount_on_the_largest_payment(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _PAID_CANCELLATIONS)

        assert rows
        for paid_at, cancelled_at, refunds, payment_id, amount, reason, refunded_at, captured, largest_id in rows:
            assert cancelled_at == paid_at + timedelta(days=1)
            assert refunds == 1
            assert (payment_id, amount, reason, refunded_at) == (largest_id, captured, "cancellation", cancelled_at)

    @pytest.mark.integration
    def test_shipments_follow_shipped_and_delivered_orders(self, month: Database) -> None:
        with month.connect() as conn:
            rows = _rows(conn, _SHIPMENTS_BY_ORDER)

        assert any(row[0] == "delivered" for row in rows)
        for status, paid, shipped, delivered, shipments, carrier, cost, shipped_at, delivered_at in rows:
            assert shipments == (1 if status in {"shipped", "delivered"} else 0)
            if shipments:
                assert carrier in {"DHL", "UPS", "DPD", "GLS"}
                assert Decimal("3.50") <= cost <= Decimal("8.00")
                assert shipped_at == shipped
                assert shipped - paid in {timedelta(days=days) for days in (1, 2, 3)}
                assert delivered_at == delivered
            if status == "delivered":
                assert delivered - shipped in {timedelta(days=days) for days in (2, 3, 4, 5)}

    def test_place_orders_without_intents_writes_nothing(self) -> None:
        changes = DayChanges()

        orders = _simulator().place_orders(_snapshot(), changes, [])

        assert orders == []
        assert changes.insert_counts() == {}
        assert changes.updates == []

    def test_web_order_tags_its_purchase_event_and_r1_duplicate_with_the_order_id_and_subtotal(self) -> None:
        changes = DayChanges()
        purchase = _purchase_event(TrackedEvent, "evt-1")
        duplicate = purchase.model_copy(update={"received_at": purchase.received_at + timedelta(seconds=30)})
        other = _purchase_event(TrackedEvent, "evt-2")
        for event in (purchase, duplicate, other):
            changes.insert(event)

        [order] = _simulator().place_orders(_snapshot(), changes, [_web_intent("evt-1", customer_id=1)])

        events = changes.inserted(TrackedEvent)
        assert [e.event_id for e in events] == ["evt-1", "evt-1", "evt-2"]
        assert [e.properties.order_id for e in events] == [order.id, order.id, None]
        assert [e.properties.cart_value for e in events] == [order.subtotal, order.subtotal, Decimal("59.99")]
        assert [e.received_at for e in events] == [purchase.received_at, duplicate.received_at, other.received_at]

    def test_web_order_leads_with_the_carted_variant_and_draws_the_other_lines_by_popularity(self) -> None:
        changes = DayChanges()
        snapshot = _snapshot()
        carted = snapshot.variants[-1]
        intents = []
        for index in range(LINE_ORDERS):
            changes.insert(_purchase_event(TrackedEvent, f"evt-{index}"))
            intents.append(_web_intent(f"evt-{index}", customer_id=1 + index % 50, variant_id=carted.id))

        orders = _simulator().place_orders(snapshot, changes, intents)

        lines: dict[int, list[OrderItem]] = defaultdict(list)
        for item in changes.inserted(OrderItem):
            lines[item.order_id].append(item)
        line_counts = Counter(len(lines[order.id]) for order in orders)
        assert all(
            (lines[o.id][0].variant_id, lines[o.id][0].unit_price) == (carted.id, carted.list_price) for o in orders
        )
        assert all(len({line.variant_id for line in lines[o.id]}) == len(lines[o.id]) for o in orders)
        assert set(line_counts) == {1, 2, 3, 4}
        assert 0.40 <= line_counts[1] / LINE_ORDERS <= 0.60  # LINE_COUNT_WEIGHTS: half the orders have one line

    def test_web_order_tags_a_pending_purchase_event(self) -> None:
        changes = DayChanges()
        changes.insert(_purchase_event(PendingEvent, "evt-late"))

        [order] = _simulator().place_orders(_snapshot(), changes, [_web_intent("evt-late", customer_id=1)])

        [event] = changes.inserted(PendingEvent)
        assert isinstance(event, PendingEvent)
        assert event.properties.order_id == order.id
        assert changes.inserted(TrackedEvent) == []

    def test_web_order_of_todays_signup_ships_to_the_new_default_address(self) -> None:
        changes = DayChanges()
        changes.insert(_purchase_event(TrackedEvent, "evt-1"))
        changes.insert(_address(77, customer_id=500, is_default=False))
        changes.insert(_address(78, customer_id=500, is_default=True))

        [order] = _simulator().place_orders(_snapshot(), changes, [_web_intent("evt-1", customer_id=500)])

        assert order.customer_id == 500
        assert order.shipping_address_id == 78

    def test_placed_orders_are_written_with_lines_status_and_payments(self) -> None:
        changes = DayChanges()
        intents = [_app_intent(customer_id, DAY_START + timedelta(hours=customer_id)) for customer_id in (1, 2, 3)]

        orders = _simulator().place_orders(_snapshot(), changes, intents)

        assert [o.customer_id for o in orders] == [1, 2, 3]
        assert changes.inserted(Order) == orders
        assert [o.order_number for o in orders] == ["SS-000001", "SS-000002", "SS-000003"]
        assert all(o.shipping_address_id == 1000 + o.customer_id for o in orders)
        assert {item.order_id for item in changes.inserted(OrderItem)} == {1, 2, 3}
        assert [(c.order_id, c.status) for c in changes.inserted(OrderStatusChange) if c.status == "placed"] == [
            (1, OrderStatus.PLACED),
            (2, OrderStatus.PLACED),
            (3, OrderStatus.PLACED),
        ]
        assert {p.order_id for p in changes.inserted(Payment)} == {1, 2, 3}

    def test_r8_stacks_a_coupon_of_another_promotion_on_the_remaining_amount(self) -> None:
        changes = DayChanges()
        automatic = _promotion(PromotionType.PERCENT, "50", promotion_id=1)
        couponed = _promotion(PromotionType.PERCENT, "50", promotion_id=2)
        coupon = CouponCode(id=9, promotion_id=2, code="SPRING25", is_single_use=False)
        snapshot = _snapshot(promotions=[automatic, couponed], coupon_codes=[coupon])
        intents = [_app_intent(1 + index % 50, DAY_START + timedelta(seconds=index)) for index in range(400)]

        orders = _simulator().place_orders(snapshot, changes, intents)

        by_order: dict[int, list[OrderDiscount]] = defaultdict(list)
        for discount in changes.inserted(OrderDiscount):
            by_order[discount.order_id].append(discount)
        stacked = [o for o in orders if len(by_order.get(o.id, [])) == 2]
        assert stacked
        assert all(len(rows) <= 2 for rows in by_order.values())
        assert all(rows[0].coupon_code_id is None for rows in by_order.values())
        for order in stacked:
            first, second = by_order[order.id]
            assert (first.promotion_id, second.promotion_id, second.coupon_code_id) == (1, 2, 9)
            assert first.amount == (order.subtotal / 2).quantize(CENT, rounding=ROUND_HALF_UP)
            assert second.amount == ((order.subtotal - first.amount) / 2).quantize(CENT, rounding=ROUND_HALF_UP)
            assert order.discount_total == first.amount + second.amount
            assert order.discount_total < order.subtotal

    def test_order_placed_just_before_midnight_is_paid_the_same_day(self) -> None:
        changes = DayChanges()
        placed_at = DAY_START + timedelta(days=1) - timedelta(seconds=30)
        intents = [_app_intent(customer_id, placed_at) for customer_id in range(1, 21)]

        orders = _simulator().place_orders(_snapshot(), changes, intents)

        paid = [c for c in changes.inserted(OrderStatusChange) if c.status == OrderStatus.PAID]
        assert paid
        assert all(placed_at <= c.changed_at < DAY_START + timedelta(days=1) for c in paid)
        assert all(p.created_at < DAY_START + timedelta(days=1) for p in changes.inserted(Payment))
        assert {o.status for o in orders} <= {OrderStatus.PLACED, OrderStatus.PAID}

    def test_progress_cancels_unpaid_order_one_day_after_placement_without_refund(self) -> None:
        changes = DayChanges()
        placed_at = DAY_START - timedelta(hours=6)
        unpaid = _order(7, OrderStatus.PLACED, placed_at)

        _simulator().progress_orders(_snapshot(open_orders=[unpaid]), changes)

        assert changes.inserted(OrderStatusChange) == [
            OrderStatusChange(id=1, order_id=7, status=OrderStatus.CANCELLED, changed_at=placed_at + timedelta(days=1))
        ]
        [update] = changes.updates
        assert update.record_type is Order
        assert update.key == {"id": 7}
        assert update.values == {"status": OrderStatus.CANCELLED, "updated_at": placed_at + timedelta(days=1)}
        assert changes.inserted(Refund) == []

    def test_paid_cancellation_refunds_the_captured_total_on_the_largest_payment(self) -> None:
        changes = DayChanges()
        order_id = _order_id_where(lambda fraction: fraction < 0.03)
        paid_at = DAY_START - timedelta(hours=2)
        order = _order(order_id, OrderStatus.PAID, paid_at, total="100.00")
        payments = [
            _payment(11, order_id, PaymentMethod.GIFT_CARD, "40.00"),
            _payment(12, order_id, PaymentMethod.CARD, "60.00"),
        ]

        _simulator().progress_orders(_snapshot(open_orders=[order], captured_payments=payments), changes)

        cancelled_at = paid_at + timedelta(days=1)
        assert changes.inserted(Refund) == [
            Refund(
                id=1,
                order_id=order_id,
                payment_id=12,
                amount=Decimal("100.00"),
                reason=RefundReason.CANCELLATION,
                refunded_at=cancelled_at,
            )
        ]
        assert [(c.status, c.changed_at) for c in changes.inserted(OrderStatusChange)] == [
            (OrderStatus.CANCELLED, cancelled_at)
        ]
        assert changes.inserted(Shipment) == []

    def test_paid_order_that_is_not_cancelled_ships_within_three_days(self) -> None:
        changes = DayChanges()
        order_id = _order_id_where(lambda fraction: fraction >= 0.03)
        paid_at = DAY_START - timedelta(days=3) + timedelta(hours=1)  # every 1-3 day delay is due by today's end

        _simulator().progress_orders(_snapshot(open_orders=[_order(order_id, OrderStatus.PAID, paid_at)]), changes)

        [shipment] = changes.inserted(Shipment)
        [status_change] = changes.inserted(OrderStatusChange)
        [update] = changes.updates
        assert shipment.order_id == order_id
        assert shipment.shipped_at - paid_at in {timedelta(days=days) for days in (1, 2, 3)}
        assert shipment.carrier in {"DHL", "UPS", "DPD", "GLS"}
        assert Decimal("3.50") <= shipment.shipping_cost <= Decimal("8.00")
        assert shipment.delivered_at is None
        assert (status_change.status, status_change.changed_at) == (OrderStatus.SHIPPED, shipment.shipped_at)
        assert update.values == {"status": OrderStatus.SHIPPED, "updated_at": shipment.shipped_at}
        assert changes.inserted(Refund) == []

    def test_progress_delivers_shipped_order_within_five_days_and_closes_its_shipment(self) -> None:
        changes = DayChanges()
        shipped_at = DAY_START - timedelta(days=5) + timedelta(hours=1)  # every 2-5 day delay is due by today's end
        order = _order(5, OrderStatus.SHIPPED, shipped_at)
        shipment = Shipment(id=3, order_id=5, carrier="DHL", shipping_cost=Decimal("4.20"), shipped_at=shipped_at)

        _simulator().progress_orders(_snapshot(open_orders=[order], open_shipments=[shipment]), changes)

        [status_change] = changes.inserted(OrderStatusChange)
        order_update, shipment_update = changes.updates
        delivered_at = status_change.changed_at
        assert status_change.status == OrderStatus.DELIVERED
        assert delivered_at - shipped_at in {timedelta(days=days) for days in (2, 3, 4, 5)}
        assert order_update.values == {"status": OrderStatus.DELIVERED, "updated_at": delivered_at}
        assert (shipment_update.record_type, shipment_update.key) == (Shipment, {"id": 3})
        assert shipment_update.values == {"delivered_at": delivered_at}

    def test_progress_never_delivers_within_two_days_of_shipping(self) -> None:
        changes = DayChanges()
        shipped_at = DAY_START - timedelta(days=1) + timedelta(hours=1)  # 2 days later is tomorrow
        order = _order(5, OrderStatus.SHIPPED, shipped_at)
        shipment = Shipment(id=3, order_id=5, carrier="DHL", shipping_cost=Decimal("4.20"), shipped_at=shipped_at)

        _simulator().progress_orders(_snapshot(open_orders=[order], open_shipments=[shipment]), changes)

        assert changes.insert_counts() == {}
        assert changes.updates == []


def _simulator() -> OrderLifecycleSimulator:
    return OrderLifecycleSimulator(
        DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({}), RealismRules(), SimulationCalendar()
    )


def _snapshot(
    day: date = DAY,
    open_orders: list[Order] | None = None,
    open_shipments: list[Shipment] | None = None,
    captured_payments: list[Payment] | None = None,
    promotions: list[Promotion] | None = None,
    coupon_codes: list[CouponCode] | None = None,
) -> StoreSnapshot:
    catalog = DayChanges()
    CatalogBuilder(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({})).build(catalog, DAY, DAY)
    variants = catalog.inserted(ProductVariant)
    people = [make_customer(customer_id) for customer_id in range(1, 51)]
    return StoreSnapshot(
        day=day,
        variants=variants,
        variant_popularity={v.id: 1 / len(variants) for v in variants},
        customers=people,
        customer_propensity={c.id: 1.0 for c in people},
        customer_devices={},
        leads=[],
        active_campaigns={},
        recent_email_sends=[],
        active_promotions=catalog.inserted(Promotion) if promotions is None else promotions,
        coupon_codes=coupon_codes or [],
        shipping_address_ids={c.id: 1000 + c.id for c in people},
        open_orders=open_orders or [],
        open_shipments=open_shipments or [],
        captured_payments=captured_payments or [],
        products=[],
        product_category_l1={},
        recently_delivered_orders=[],
        recently_delivered_items=[],
        recently_delivered_payments=[],
        open_returns=[],
    )


def _order_id_where(accept: Callable[[float], bool]) -> int:
    random = DeterministicRandom(SIMULATION_SEED)
    return next(i for i in range(1, 10_000) if accept(random.entity_fraction("order", i, PAID_CANCELLATION_PURPOSE)))


def _order(order_id: int, status: OrderStatus, updated_at: datetime, total: str = "64.95") -> Order:
    return Order(
        id=order_id,
        order_number=f"SS-{order_id:06d}",
        customer_id=1,
        shipping_address_id=1001,
        status=status,
        subtotal=Decimal("50.00"),
        discount_total=Decimal("0.00"),
        shipping_amount=Decimal("4.95"),
        tax_amount=Decimal("10.00"),
        order_total=Decimal(total),
        sales_channel=SalesChannel.APP,
        placed_at=updated_at - timedelta(minutes=10),
        updated_at=updated_at,
    )


def _payment(payment_id: int, order_id: int, method: PaymentMethod, amount: str) -> Payment:
    return Payment(
        id=payment_id,
        order_id=order_id,
        method=method,
        amount=Decimal(amount),
        status=PaymentStatus.CAPTURED,
        created_at=DAY_START - timedelta(days=1),
    )


def _promotion(promotion_type: PromotionType, value: str, promotion_id: int = 1) -> Promotion:
    return Promotion(
        id=promotion_id,
        name="Test",
        type=promotion_type,
        value=Decimal(value),
        min_order_value=Decimal(0),
        starts_at=DAY_START,
        ends_at=DAY_START + timedelta(days=1),
    )


def _item(item_id: int, quantity: int, unit_price: str) -> OrderItem:
    return OrderItem(id=item_id, order_id=1, variant_id=item_id, quantity=quantity, unit_price=Decimal(unit_price))


def _address(address_id: int, customer_id: int, *, is_default: bool) -> Address:
    return Address(
        id=address_id,
        customer_id=customer_id,
        type=AddressType.SHIPPING,
        country_code="DE",
        city="Berlin",
        postal_code="10115",
        is_default=is_default,
    )


def _purchase_event(event_type: type[TrackedEvent], event_id: str) -> TrackedEvent:
    received_at = DAY_START + timedelta(hours=12)
    return event_type(
        event_id=event_id,
        anonymous_id="device-1",
        user_id="1",
        event_type=EventType.PURCHASE,
        occurred_at=received_at.isoformat(),
        received_at=received_at,
        context=EventContext(device_type=DeviceType.MOBILE),
        properties=EventProperties(page_path="/checkout", cart_value=Decimal("59.99")),
    )


def _web_intent(event_id: str, customer_id: int, variant_id: int = 1) -> PurchaseIntent:
    return PurchaseIntent(
        customer_id=customer_id,
        sales_channel=SalesChannel.WEB,
        purchased_at=DAY_START + timedelta(hours=12),
        session_anonymous_id="device-1",
        utm_campaign=None,
        purchase_event_id=event_id,
        variant_id=variant_id,
    )


def _app_intent(customer_id: int, purchased_at: datetime) -> PurchaseIntent:
    return PurchaseIntent(
        customer_id=customer_id,
        sales_channel=SalesChannel.APP,
        purchased_at=purchased_at,
        session_anonymous_id=None,
        utm_campaign=None,
        purchase_event_id=None,
        variant_id=None,
    )


def _rows(conn: psycopg.Connection, query: str) -> list[tuple[Any, ...]]:
    return conn.execute(query).fetchall()  # type: ignore[arg-type]  # static SQL only


_ORDER_TOTALS = """
SELECT o.order_number, o.subtotal, o.discount_total, o.shipping_amount, o.tax_amount, o.order_total,
       (SELECT sum(i.quantity * i.unit_price) FROM raw_shop.order_items i WHERE i.order_id = o.id),
       (SELECT coalesce(sum(d.amount), 0) FROM raw_shop.order_discounts d WHERE d.order_id = o.id),
       (SELECT count(*) FROM raw_shop.order_items i WHERE i.order_id = o.id),
       EXISTS (SELECT 1 FROM raw_shop.order_discounts d JOIN raw_shop.promotions p ON p.id = d.promotion_id
               WHERE d.order_id = o.id AND p.type = 'free_shipping')
FROM raw_shop.orders o ORDER BY o.id
"""

_PAYMENTS_BY_ORDER = """
SELECT o.id,
       EXISTS (SELECT 1 FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'paid'),
       o.order_total,
       coalesce((SELECT sum(p.amount) FROM raw_shop.payments p WHERE p.order_id = o.id AND p.status = 'captured'), 0),
       (SELECT count(*) FROM raw_shop.payments p WHERE p.order_id = o.id AND p.status = 'captured'),
       (SELECT count(*) FROM raw_shop.payments p WHERE p.order_id = o.id AND p.status = 'failed')
FROM raw_shop.orders o ORDER BY o.id
"""

_PURCHASE_EVENTS = """
SELECT event_id, (properties->>'order_id')::bigint AS order_id
FROM raw_events.events WHERE event_type = 'purchase'
UNION
SELECT event_id, (properties->>'order_id')::bigint FROM raw_meta.pending_events WHERE event_type = 'purchase'
"""

_WEB_ORDER_PURCHASES = """
WITH events AS (
    SELECT DISTINCT ON (event_id) event_id, anonymous_id, event_type, occurred_at::timestamptz AS occurred_at,
           properties
    FROM (SELECT * FROM raw_events.events UNION ALL SELECT * FROM raw_meta.pending_events) e
    ORDER BY event_id
)
SELECT o.subtotal, (p.properties->>'cart_value')::numeric(12, 2),
       (SELECT array_agg(i.variant_id ORDER BY i.id) FROM raw_shop.order_items i WHERE i.order_id = o.id),
       (SELECT array_agg((c.properties->>'variant_id')::bigint) FROM events c
        WHERE c.event_type = 'add_to_cart' AND c.anonymous_id = p.anonymous_id
          AND c.occurred_at BETWEEN p.occurred_at - %s * interval '1 second' AND p.occurred_at)
FROM raw_shop.orders o
JOIN events p ON p.event_type = 'purchase' AND (p.properties->>'order_id')::bigint = o.id
WHERE o.sales_channel = 'web'
ORDER BY o.id
"""

_PURCHASE_EVENTS_BY_ORDER = f"""
SELECT o.id, o.sales_channel, count(DISTINCT e.event_id)
FROM raw_shop.orders o LEFT JOIN ({_PURCHASE_EVENTS}) e ON e.order_id = o.id
GROUP BY o.id, o.sales_channel ORDER BY o.id
"""

_NEVER_PAID_ORDERS = """
SELECT o.id, o.status, o.placed_at,
       (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'cancelled'),
       (SELECT array_agg(p.status::text ORDER BY p.id) FROM raw_shop.payments p WHERE p.order_id = o.id),
       (SELECT count(*) FROM raw_shop.refunds r WHERE r.order_id = o.id)
FROM raw_shop.orders o
WHERE NOT EXISTS (SELECT 1 FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'paid')
ORDER BY o.id
"""

_STACKED_DISCOUNTS = """
SELECT o.subtotal, o.discount_total, array_agg(d.amount ORDER BY d.id), array_agg(d.promotion_id ORDER BY d.id),
       array_agg(d.coupon_code_id ORDER BY d.id), array_agg(c.promotion_id ORDER BY d.id)
FROM raw_shop.orders o JOIN raw_shop.order_discounts d ON d.order_id = o.id
LEFT JOIN raw_shop.coupon_codes c ON c.id = d.coupon_code_id
GROUP BY o.id HAVING count(*) > 1 ORDER BY o.id
"""

_PAID_CANCELLATIONS = """
SELECT (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'paid'),
       (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'cancelled'),
       (SELECT count(*) FROM raw_shop.refunds r WHERE r.order_id = o.id),
       r.payment_id, r.amount, r.reason, r.refunded_at,
       (SELECT sum(p.amount) FROM raw_shop.payments p WHERE p.order_id = o.id AND p.status = 'captured'),
       (SELECT p.id FROM raw_shop.payments p WHERE p.order_id = o.id AND p.status = 'captured'
        ORDER BY p.amount DESC, p.id LIMIT 1)
FROM raw_shop.orders o LEFT JOIN raw_shop.refunds r ON r.order_id = o.id
WHERE o.status = 'cancelled'
  AND EXISTS (SELECT 1 FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'paid')
ORDER BY o.id
"""

_SHIPMENTS_BY_ORDER = """
SELECT o.status,
       (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'paid'),
       (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'shipped'),
       (SELECT h.changed_at FROM raw_shop.order_status_history h WHERE h.order_id = o.id AND h.status = 'delivered'),
       (SELECT count(*) FROM raw_shop.shipments s WHERE s.order_id = o.id),
       s.carrier, s.shipping_cost, s.shipped_at, s.delivered_at
FROM raw_shop.orders o LEFT JOIN raw_shop.shipments s ON s.order_id = o.id
ORDER BY o.id
"""
