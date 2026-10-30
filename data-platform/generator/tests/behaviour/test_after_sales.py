from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from helpers import (
    SIMULATION_SEED,
    assert_rate,
    fetch_all,
    make_item,
    make_order,
    make_payment,
    make_snapshot,
    run_days,
)
from sherpa_generator.behaviour.after_sales import OUTCOME_PURPOSE, REJECTION_PURPOSE, AfterSalesSimulator
from sherpa_generator.records import (
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    ProductVariant,
    Refund,
    RefundReason,
    Return,
    ReturnItem,
    ReturnReason,
    ReturnStatus,
)
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_generator.database.connection import Database
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

DELIVERED_AT = datetime(2025, 3, 1, 15, 30, tzinfo=UTC)
DELIVERY_DAY = date(2025, 3, 1)
FOOTWEAR_PRODUCT = 1
APPAREL_PRODUCT = 2
RETURN_RATE = 0.12
GOODWILL_RATE = 0.01
REJECTION_RATE = 0.10
RATE_ORDERS = 6_000  # ~720 returns and ~60 goodwill refunds: enough for assert_rate to tell 1 % and 90 % from 0
DECISION_DAYS = 36  # a request falls up to 30 days after delivery and is decided up to 2 days after that


class TestAfterSalesSimulator:
    def test_return_refund_uses_allocated_discount(self) -> None:
        items = [_item(1, 1, "60.00"), _item(2, 1, "40.00")]

        amount = _simulator().return_refund_amount(items, [Decimal("6.00"), Decimal("4.00")], {1: 1})

        assert amount == Decimal("64.80")  # (60 - 6) x 1.20

    def test_return_refund_of_a_partial_quantity_takes_the_per_unit_share_and_rounds_half_up_once(self) -> None:
        items = [_item(1, 4, "10.00")]

        amount = _simulator().return_refund_amount(items, [Decimal("0.05")], {1: 1})

        assert amount == Decimal("11.99")  # (10 - 0.0125) x 1.20 = 11.985; half-even would give 11.98

    def test_return_refund_of_every_unit_is_the_net_order_value_with_tax(self) -> None:
        items = [_item(1, 2, "19.99"), _item(2, 3, "5.01")]

        amount = _simulator().return_refund_amount(items, [Decimal("2.66"), Decimal("1.00")], {1: 2, 2: 3})

        assert amount == Decimal("61.62")  # (39.98 + 15.03 - 3.66) x 1.20: subtotal - discount + tax

    def test_return_refund_ignores_lines_that_are_not_returned(self) -> None:
        items = [_item(1, 1, "60.00"), _item(2, 2, "40.00")]
        allocated = [Decimal("6.00"), Decimal("8.00")]

        none_returned = _simulator().return_refund_amount(items, allocated, {})
        second_line = _simulator().return_refund_amount(items, allocated, {2: 1})

        assert none_returned == Decimal("0.00")
        assert second_line == Decimal("43.20")  # (40 - 8/2) x 1.20
        assert _simulator().return_refund_amount([], [], {}) == Decimal("0.00")

    def test_goodwill_never_refunds_shipping(self) -> None:
        order = _order(1, subtotal="100.00", discount="10.00", shipping="4.95", tax="18.00")

        lowest = _simulator().goodwill_amount(order, 0.0)
        middle = _simulator().goodwill_amount(order, 0.5)

        assert lowest == Decimal("5.40")  # 5 % of 100 - 10 + 18, without the 4.95 shipping
        assert middle == Decimal("13.50")  # 12.5 %

    def test_goodwill_share_stays_below_twenty_percent_and_rounds_half_up(self) -> None:
        order = _order(1, subtotal="10.10", discount="0.00", shipping="4.95", tax="0.00")
        large = _order(2, subtotal="100.00", discount="0.00", shipping="0.00", tax="20.00")

        assert _simulator().goodwill_amount(order, 0.0) == Decimal("0.51")  # 0.505; half-even would give 0.50
        assert _simulator().goodwill_amount(large, 1 - 2**-53) == Decimal("24.00")  # just below 20 % of 120
        assert _simulator().goodwill_amount(large, 0.9) < Decimal("24.00")

    def test_returning_order_is_requested_once_within_thirty_days_of_delivery(self) -> None:
        order = _order(_order_id_where(lambda outcome, _: outcome < RETURN_RATE))

        changes_by_day = run_days(_simulator().simulate_day, _delivered_snapshot(order), first=DELIVERY_DAY, days=40)

        [(day, changes)] = [(day, c) for day, c in changes_by_day if c.inserted(Return)]
        [request] = changes.inserted(Return)
        returned = changes.inserted(ReturnItem)
        assert request.order_id == order.id
        assert (request.status, request.updated_at) == (ReturnStatus.REQUESTED, request.requested_at)
        assert request.requested_at - DELIVERED_AT in {timedelta(days=days) for days in range(1, 31)}
        assert request.requested_at.date() == day
        assert request.reason in {ReturnReason.DAMAGED, ReturnReason.NOT_AS_DESCRIBED, ReturnReason.CHANGED_MIND}
        assert returned
        assert {r.return_id for r in returned} == {request.id}
        assert {r.order_item_id for r in returned} <= {10, 11}
        assert all(1 <= r.quantity <= 2 for r in returned)
        assert all(not c.updates and not c.inserted(Refund) for _, c in changes_by_day)

    def test_footwear_return_is_for_size(self) -> None:
        order = _order(_order_id_where(lambda outcome, _: outcome < RETURN_RATE))
        items = [_item(10, 1, "89.99", order_id=order.id, variant_id=101)]

        changes_by_day = run_days(
            _simulator().simulate_day, _delivered_snapshot(order, items), first=DELIVERY_DAY, days=31
        )

        [request] = [r for _, c in changes_by_day for r in c.inserted(Return)]
        assert request.reason == ReturnReason.SIZE

    def test_return_rate_is_doubled_for_footwear_orders(self) -> None:
        order_ids = range(1, 4001)
        footwear = [_order(i) for i in order_ids if i % 2]
        apparel = [_order(i) for i in order_ids if not i % 2]

        footwear_rate = len(_requests(footwear, variant_id=101)[0]) / len(footwear)
        apparel_rate = len(_requests(apparel, variant_id=201)[0]) / len(apparel)

        assert 0.09 <= apparel_rate <= 0.15
        assert 0.20 <= footwear_rate <= 0.28

    def test_non_footwear_reasons_and_restockable_share(self, season: _Season) -> None:
        requests, returned = season.requests, season.returned_items

        reasons = Counter(r.reason for r in requests)
        restockable = sum(r.is_restockable for r in returned) / len(returned)
        assert len(returned) == len(requests)  # one line per order here
        assert set(reasons) == {ReturnReason.DAMAGED, ReturnReason.NOT_AS_DESCRIBED, ReturnReason.CHANGED_MIND}
        assert 0.13 <= reasons[ReturnReason.DAMAGED] / len(requests) <= 0.27
        assert 0.22 <= reasons[ReturnReason.NOT_AS_DESCRIBED] / len(requests) <= 0.38
        assert 0.41 <= reasons[ReturnReason.CHANGED_MIND] / len(requests) <= 0.59
        assert 0.72 <= restockable <= 0.88

    def test_approved_return_is_received_three_to_ten_days_after_request_and_refunded_on_largest_payment(self) -> None:
        order = _order(_order_id_where(lambda outcome, rejection: outcome < RETURN_RATE and rejection >= 0.10))
        request, returned = _requested_return(order)
        open_return = request.model_copy(update={"status": ReturnStatus.APPROVED})

        changes_by_day = run_days(
            _simulator().simulate_day,
            _delivered_snapshot(order, open_returns=[open_return]),
            first=request.requested_at.date(),
            days=12,
        )

        [(_, changes)] = [(day, c) for day, c in changes_by_day if c.updates]
        [update] = changes.updates
        [refund] = changes.inserted(Refund)
        received_at = update.values["updated_at"]
        assert isinstance(received_at, datetime)
        assert (update.record_type, update.key) == (Return, {"id": request.id})
        assert update.values["status"] == ReturnStatus.RECEIVED
        assert received_at - request.requested_at in {timedelta(days=days) for days in range(3, 11)}
        assert refund.order_id == order.id
        assert refund.return_id == request.id
        assert refund.payment_id == 21
        assert refund.reason == RefundReason.RETURN
        assert refund.refunded_at == received_at
        assert refund.amount == _simulator().return_refund_amount(
            _items(order.id), [Decimal("6.00"), Decimal("4.00")], {r.order_item_id: r.quantity for r in returned}
        )

    def test_return_decision_falls_one_or_two_days_after_request(self) -> None:
        order = _order(_order_id_where(lambda outcome, rejection: outcome < RETURN_RATE and rejection >= 0.10))
        request, _ = _requested_return(order)

        changes_by_day = run_days(
            _simulator().simulate_day,
            _delivered_snapshot(order, open_returns=[request]),
            first=request.requested_at.date(),
            days=12,
        )

        [(_, changes)] = [(day, c) for day, c in changes_by_day if c.updates]
        [update] = changes.updates
        decided_at = update.values["updated_at"]
        assert isinstance(decided_at, datetime)
        assert update.values["status"] == ReturnStatus.APPROVED
        assert decided_at - request.requested_at in {timedelta(days=1), timedelta(days=2)}
        assert all(not c.inserted(Refund) for _, c in changes_by_day)

    def test_rejected_return_is_closed_without_refund(self) -> None:
        order = _order(_order_id_where(lambda outcome, rejection: outcome < RETURN_RATE and rejection < 0.10))
        request, _ = _requested_return(order)

        changes_by_day = run_days(
            _simulator().simulate_day,
            _delivered_snapshot(order, open_returns=[request]),
            first=request.requested_at.date(),
            days=12,
        )

        [update] = [u for _, c in changes_by_day for u in c.updates]
        assert update.values["status"] == ReturnStatus.REJECTED
        assert all(not c.inserted(Refund) for _, c in changes_by_day)

    def test_goodwill_refund_falls_one_to_twenty_days_after_delivery(self) -> None:
        order = _order(
            _order_id_where(lambda outcome, _: RETURN_RATE <= outcome < RETURN_RATE + GOODWILL_RATE),
            subtotal="100.00",
            discount="10.00",
            shipping="4.95",
            tax="18.00",
        )

        changes_by_day = run_days(_simulator().simulate_day, _delivered_snapshot(order), first=DELIVERY_DAY, days=40)

        [refund] = [r for _, c in changes_by_day for r in c.inserted(Refund)]
        assert (refund.order_id, refund.return_id, refund.payment_id) == (order.id, None, 21)
        assert refund.reason == RefundReason.GOODWILL
        assert refund.refunded_at - DELIVERED_AT in {timedelta(days=days) for days in range(1, 21)}
        assert Decimal("5.40") <= refund.amount <= Decimal("21.60")  # 5-20 % of 108
        assert all(not c.inserted(Return) for _, c in changes_by_day)

    def test_goodwill_refunds_hit_one_percent_of_delivered_orders(self, season: _Season) -> None:
        goodwill = [r for _, c in season.first_run for r in c.inserted(Refund) if r.reason == RefundReason.GOODWILL]

        assert_rate(len(goodwill), RATE_ORDERS, 0.01)  # R9, Spec A §5.2 step 7: 1 %
        assert len({r.order_id for r in goodwill}) == len(goodwill)

    def test_nine_in_ten_decided_returns_are_approved(self, season: _Season) -> None:
        decisions = [u.values["status"] for _, c in season.decision_run for u in c.updates if u.record_type is Return]

        approved = sum(1 for status in decisions if status == ReturnStatus.APPROVED)

        assert len(decisions) == len(season.requests)  # every request got decided in the window
        assert_rate(approved, len(decisions), 0.90)  # R9, Spec A §5.2 step 7: 90 % approved

    def test_order_without_after_sales_writes_nothing(self) -> None:
        order = _order(_order_id_where(lambda outcome, _: outcome >= RETURN_RATE + GOODWILL_RATE))

        changes_by_day = run_days(_simulator().simulate_day, _delivered_snapshot(order), first=DELIVERY_DAY, days=45)

        assert all(c.insert_counts() == {} and c.updates == [] for _, c in changes_by_day)

    @pytest.mark.integration
    def test_refunds_never_exceed_captured_per_order(self, after_sales_season: Database) -> None:
        with after_sales_season.connect() as conn:
            rows = fetch_all(conn, _REFUNDS_VS_CAPTURED)

        reasons = {reason for _, _, reasons in rows for reason in reasons}
        assert reasons == {"cancellation", "return", "goodwill"}
        assert all(refunded <= captured for refunded, captured, _ in rows)

    @pytest.mark.integration
    def test_rejected_returns_get_no_refund(self, after_sales_season: Database) -> None:
        with after_sales_season.connect() as conn:
            rows = fetch_all(conn, _REFUNDS_BY_RETURN)

        statuses = {status for status, _, _ in rows}
        # "requested" is transient: it only exists for returns raised at the very end of the season.
        assert {"approved", "received", "rejected"} <= statuses <= {"requested", "approved", "received", "rejected"}
        for status, refunds, refunded_at_is_receipt in rows:
            assert refunds == (1 if status == "received" else 0)
            assert refunded_at_is_receipt in {None, True}

    @pytest.mark.integration
    def test_return_requested_only_after_delivery(self, after_sales_season: Database) -> None:
        with after_sales_season.connect() as conn:
            rows = fetch_all(conn, _RETURN_TIMING)

        assert rows
        for order_status, requested_at, delivered_at, items, items_of_order, over_returned in rows:
            assert order_status == "delivered"
            assert timedelta(days=1) <= requested_at - delivered_at <= timedelta(days=30)
            assert 1 <= items <= items_of_order
            assert not over_returned


@dataclass(frozen=True)
class _Season:
    first_run: list[tuple[date, DayChanges]]
    decision_run: list[tuple[date, DayChanges]]
    requests: list[Return]
    returned_items: list[ReturnItem]


@pytest.fixture(scope="module")
def season() -> _Season:
    """RATE_ORDERS delivered single-line apparel orders over their whole return window, then the requested orders
    again with their requests open so the decisions fall due. Read-only: tests only measure it."""
    orders = [_order(order_id) for order_id in range(1, RATE_ORDERS + 1)]
    items = [_item(o.id, 1, "50.00", order_id=o.id, variant_id=201) for o in orders]
    payments = [_payment(o.id, o.id, PaymentMethod.CARD, "108.00") for o in orders]
    snapshot = _delivered_snapshot(orders[0], items).model_copy(
        update={"recently_delivered_orders": orders, "recently_delivered_payments": payments}
    )
    first_run = run_days(_simulator().simulate_day, snapshot, first=DELIVERY_DAY, days=31)
    requests = [r for _, c in first_run for r in c.inserted(Return)]
    returned_items = [r for _, c in first_run for r in c.inserted(ReturnItem)]
    requested = {r.order_id for r in requests}
    decision_snapshot = snapshot.model_copy(
        update={
            "open_returns": requests,
            "recently_delivered_orders": [o for o in orders if o.id in requested],
            "recently_delivered_items": [i for i in items if i.order_id in requested],
            "recently_delivered_payments": [p for p in payments if p.order_id in requested],
        }
    )
    decision_run = run_days(_simulator().simulate_day, decision_snapshot, DELIVERY_DAY, DECISION_DAYS)
    return _Season(first_run, decision_run, requests, returned_items)


def _simulator() -> AfterSalesSimulator:
    return AfterSalesSimulator(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({}), SimulationCalendar())


def _requested_return(order: Order) -> tuple[Return, list[ReturnItem]]:
    changes_by_day = run_days(_simulator().simulate_day, _delivered_snapshot(order), first=DELIVERY_DAY, days=31)
    [changes] = [c for _, c in changes_by_day if c.inserted(Return)]
    [request] = changes.inserted(Return)
    return request, changes.inserted(ReturnItem)


def _requests(orders: list[Order], variant_id: int) -> tuple[list[Return], list[ReturnItem]]:
    """Return requests of single-line orders, all of the same variant."""
    items = [_item(order.id, 1, "50.00", order_id=order.id, variant_id=variant_id) for order in orders]
    payments = [_payment(order.id, order.id, PaymentMethod.CARD, "108.00") for order in orders]
    snapshot = _delivered_snapshot(orders[0], items).model_copy(
        update={"recently_delivered_orders": orders, "recently_delivered_payments": payments}
    )
    changes_by_day = run_days(_simulator().simulate_day, snapshot, first=DELIVERY_DAY, days=31)
    return [r for _, c in changes_by_day for r in c.inserted(Return)], [
        r for _, c in changes_by_day for r in c.inserted(ReturnItem)
    ]


def _delivered_snapshot(
    order: Order, items: list[OrderItem] | None = None, open_returns: list[Return] | None = None
) -> StoreSnapshot:
    return make_snapshot(
        DELIVERY_DAY,
        variants=[_variant(101, FOOTWEAR_PRODUCT), _variant(201, APPAREL_PRODUCT)],
        product_category_l1={FOOTWEAR_PRODUCT: "Footwear", APPAREL_PRODUCT: "Apparel"},
        recently_delivered_orders=[order],
        recently_delivered_items=_items(order.id) if items is None else items,
        recently_delivered_payments=[
            _payment(20, order.id, PaymentMethod.GIFT_CARD, "40.00"),
            _payment(21, order.id, PaymentMethod.CARD, "68.00"),
        ],
        open_returns=open_returns or [],
    )


def _order_id_where(accept: Callable[[float, float], bool]) -> int:
    random = DeterministicRandom(SIMULATION_SEED)
    return next(
        i
        for i in range(1, 100_000)
        if accept(
            random.entity_fraction("order", i, OUTCOME_PURPOSE), random.entity_fraction("order", i, REJECTION_PURPOSE)
        )
    )


def _order(
    order_id: int,
    subtotal: str = "100.00",
    discount: str = "10.00",
    shipping: str = "0.00",
    tax: str = "18.00",
) -> Order:
    total = Decimal(subtotal) - Decimal(discount) + Decimal(shipping) + Decimal(tax)
    return make_order(
        order_id,
        status=OrderStatus.DELIVERED,
        subtotal=Decimal(subtotal),
        discount_total=Decimal(discount),
        shipping_amount=Decimal(shipping),
        tax_amount=Decimal(tax),
        order_total=total,
        placed_at=DELIVERED_AT - timedelta(days=5),
        updated_at=DELIVERED_AT,
    )


def _items(order_id: int) -> list[OrderItem]:
    """Apparel lines of 60 and 40 EUR (the 10 EUR order discount splits 6 / 4)."""
    return [
        _item(10, 1, "60.00", order_id=order_id, variant_id=201),
        _item(11, 2, "20.00", order_id=order_id, variant_id=201),
    ]


def _item(item_id: int, quantity: int, unit_price: str, order_id: int = 1, variant_id: int = 201) -> OrderItem:
    return make_item(
        item_id, order_id=order_id, variant_id=variant_id, quantity=quantity, unit_price=Decimal(unit_price)
    )


def _payment(payment_id: int, order_id: int, method: PaymentMethod, amount: str) -> Payment:
    return make_payment(
        payment_id,
        order_id=order_id,
        method=method,
        amount=Decimal(amount),
        created_at=DELIVERED_AT - timedelta(days=5),
    )


def _variant(variant_id: int, product_id: int) -> ProductVariant:
    return ProductVariant(
        id=variant_id,
        product_id=product_id,
        sku=f"SKU-{variant_id}",
        size="42",
        color="Black",
        cost_price=Decimal("20.00"),
        list_price=Decimal("50.00"),
        created_at=DELIVERED_AT - timedelta(days=60),
        updated_at=DELIVERED_AT - timedelta(days=60),
    )


_REFUNDS_VS_CAPTURED = """
SELECT sum(r.amount),
       (SELECT sum(p.amount) FROM raw_shop.payments p WHERE p.order_id = r.order_id AND p.status = 'captured'),
       array_agg(DISTINCT r.reason::text)
FROM raw_shop.refunds r GROUP BY r.order_id ORDER BY r.order_id
"""

_REFUNDS_BY_RETURN = """
SELECT rt.status, count(rf.id), bool_and(rf.refunded_at = rt.updated_at)
FROM raw_shop.returns rt LEFT JOIN raw_shop.refunds rf ON rf.return_id = rt.id
GROUP BY rt.id, rt.status ORDER BY rt.id
"""

_RETURN_TIMING = """
SELECT o.status, rt.requested_at, s.delivered_at,
       (SELECT count(*) FROM raw_shop.return_items ri WHERE ri.return_id = rt.id),
       (SELECT count(*) FROM raw_shop.order_items i WHERE i.order_id = o.id),
       EXISTS (SELECT 1 FROM raw_shop.return_items ri JOIN raw_shop.order_items i ON i.id = ri.order_item_id
               WHERE ri.return_id = rt.id AND (ri.quantity > i.quantity OR ri.quantity < 1 OR i.order_id <> o.id))
FROM raw_shop.returns rt JOIN raw_shop.orders o ON o.id = rt.order_id
JOIN raw_shop.shipments s ON s.order_id = o.id
ORDER BY rt.id
"""
