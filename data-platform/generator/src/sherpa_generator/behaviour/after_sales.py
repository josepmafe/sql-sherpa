from __future__ import annotations

from bisect import bisect
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import accumulate
from typing import TYPE_CHECKING, Final

from sherpa_generator.behaviour.order_lifecycle import TAX_RATE, OrderPricing
from sherpa_generator.records import (
    Refund,
    RefundReason,
    Return,
    ReturnItem,
    ReturnReason,
    ReturnStatus,
)
from sherpa_generator.records.base import quantize_money
from sherpa_generator.simulation.realism_rules import RealismRules

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sherpa_generator.records import Money, Order, OrderItem, Payment
    from sherpa_generator.simulation.calendar import SimulationCalendar
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

FOOTWEAR: Final = "Footwear"  # the L1 category whose returns are doubled and always for size (R9)
REQUEST_DELAY_DAYS: Final = (1, 30)  # Spec A §5.2 step 7: requested within 30 days of delivery
NON_SIZE_REASON_WEIGHTS: Final = {
    ReturnReason.DAMAGED: 0.2,
    ReturnReason.NOT_AS_DESCRIBED: 0.3,
    ReturnReason.CHANGED_MIND: 0.5,
}
REJECTION_RATE: Final = 1 - RealismRules.RETURN_APPROVAL_RATE  # Spec A §5.2 step 7: 90 % approved
DECISION_DELAY_DAYS: Final = (1, 2)  # approved or rejected 1-2 days after the request
RECEIPT_DELAY_DAYS: Final = (3, 10)  # Spec A §5.2 step 7: received (and refunded) 3-10 days after the request
RESTOCKABLE_RATE: Final = 0.8
GOODWILL_DELAY_DAYS: Final = (1, 20)
# entity_fraction purposes. One outcome fraction per order decides return, goodwill or neither, so the two never
# meet on one order and its refunds stay within what was captured.
OUTCOME_PURPOSE: Final = "after_sales"
REQUEST_DELAY_PURPOSE: Final = "return_delay"
LINE_COUNT_PURPOSE: Final = "return_lines"
REASON_PURPOSE: Final = "return_reason"
REJECTION_PURPOSE: Final = "return_decision"
DECISION_DELAY_PURPOSE: Final = "return_decision_delay"
RECEIPT_DELAY_PURPOSE: Final = "return_receipt_delay"
GOODWILL_DELAY_PURPOSE: Final = "goodwill_delay"
GOODWILL_SHARE_PURPOSE: Final = "goodwill_share"
LINE_RANK_PURPOSE: Final = "return_rank"
LINE_QUANTITY_PURPOSE: Final = "return_quantity"
RESTOCKABLE_PURPOSE: Final = "restockable"
_ORDER: Final = "order"
_ORDER_ITEM: Final = "order_item"
_REASONS: Final = tuple(NON_SIZE_REASON_WEIGHTS)
_REASON_CUM_WEIGHTS: Final = tuple(accumulate(NON_SIZE_REASON_WEIGHTS.values()))
_GOODWILL_MIN_SHARE: Final = Decimal(str(RealismRules.GOODWILL_REFUND_MIN_SHARE))
_GOODWILL_SHARE_RANGE: Final = Decimal(str(RealismRules.GOODWILL_REFUND_MAX_SHARE)) - _GOODWILL_MIN_SHARE


class AfterSalesSimulator:
    """Spec A §5.2 step 7 (R9): returns with their approval, receipt and refund, and goodwill refunds.

    Everything is decided at delivery by `entity_fraction` and written on the day it falls on, so no timestamp lies
    beyond the simulated day and a resumed run equals one long run.
    """

    def __init__(self, random: DeterministicRandom, keys: PrimaryKeyAllocator, calendar: SimulationCalendar) -> None:
        self._random = random
        self._keys = keys
        self._calendar = calendar
        self._pricing = OrderPricing()

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        start, end = self._calendar.day_bounds(snapshot.day)
        day = _AfterSalesDay(snapshot, changes, start, end)
        for order in snapshot.recently_delivered_orders:
            fraction = self._fraction(order.id, OUTCOME_PURPOSE)
            return_rate = RealismRules.RETURN_RATE * (
                RealismRules.FOOTWEAR_RETURN_MULTIPLIER if day.has_footwear(order.id) else 1
            )
            if fraction < return_rate:
                self._request_return(day, order)
            elif fraction < return_rate + RealismRules.GOODWILL_REFUND_RATE:
                self._refund_goodwill(day, order)

        for open_return in snapshot.open_returns:
            self._progress_return(day, open_return)

    def return_refund_amount(
        self, items: Sequence[OrderItem], allocated: Sequence[Money], returned_qty: Mapping[int, int]
    ) -> Money:
        """Net value of the returned units after their share of the order discount, plus tax (Spec A §5.2 step 7).

        `allocated` is each line's share of the discount; rounding happens once, on the total.
        """
        net = sum(
            (
                returned * item.unit_price - share * returned / item.quantity
                for item, share in zip(items, allocated, strict=True)
                if (returned := returned_qty.get(item.id, 0))
            ),
            Decimal(0),
        )
        return quantize_money(net * (1 + TAX_RATE))

    def goodwill_amount(self, order: Order, fraction: float) -> Money:
        """5-20 % of what the customer paid for the goods, never the shipping (Spec A §5.2 step 7)."""
        share = _GOODWILL_MIN_SHARE + Decimal(fraction) * _GOODWILL_SHARE_RANGE
        return quantize_money((order.subtotal - order.discount_total + order.tax_amount) * share)

    def _request_return(self, day: _AfterSalesDay, order: Order) -> None:
        if not day.includes(self._requested_at(order)):
            return

        plan = self._return_plan(day, order)
        return_id = self._keys.next_id(Return)
        day.changes.insert(
            Return(
                id=return_id,
                order_id=order.id,
                reason=plan.reason,
                status=ReturnStatus.REQUESTED,
                requested_at=plan.requested_at,
                updated_at=plan.requested_at,
            )
        )
        for item_id, quantity in plan.quantities.items():
            day.changes.insert(
                ReturnItem(
                    id=self._keys.next_id(ReturnItem),
                    return_id=return_id,
                    order_item_id=item_id,
                    quantity=quantity,
                    is_restockable=self._fraction(item_id, RESTOCKABLE_PURPOSE, _ORDER_ITEM) < RESTOCKABLE_RATE,
                )
            )

    def _progress_return(self, day: _AfterSalesDay, open_return: Return) -> None:
        """Requested returns are approved or rejected; approved ones are received and refunded."""
        order = day.orders[open_return.order_id]
        plan = self._return_plan(day, order)
        match open_return.status:
            case ReturnStatus.REQUESTED if day.includes(plan.decided_at):
                status = ReturnStatus.REJECTED if plan.rejected else ReturnStatus.APPROVED
                day.changes.update(Return, open_return.id, status=status, updated_at=plan.decided_at)
            case ReturnStatus.APPROVED if day.includes(plan.received_at):
                day.changes.update(Return, open_return.id, status=ReturnStatus.RECEIVED, updated_at=plan.received_at)
                items = day.items[order.id]
                allocated = self._pricing.allocate_discount(
                    [item.unit_price * item.quantity for item in items], order.discount_total
                )
                amount = self.return_refund_amount(items, allocated, plan.quantities)
                self._insert_refund(day, order, amount, RefundReason.RETURN, plan.received_at, open_return.id)

    def _refund_goodwill(self, day: _AfterSalesDay, order: Order) -> None:
        refunded_at = order.updated_at + self._days(order.id, GOODWILL_DELAY_PURPOSE, GOODWILL_DELAY_DAYS)
        if day.includes(refunded_at):
            amount = self.goodwill_amount(order, self._fraction(order.id, GOODWILL_SHARE_PURPOSE))
            self._insert_refund(day, order, amount, RefundReason.GOODWILL, refunded_at)

    def _insert_refund(
        self,
        day: _AfterSalesDay,
        order: Order,
        amount: Money,
        reason: RefundReason,
        refunded_at: datetime,
        return_id: int | None = None,
    ) -> None:
        """Booked against the order's largest captured payment, like a cancellation refund."""
        payment = max(day.payments[order.id], key=lambda p: p.amount)
        day.changes.insert(
            Refund(
                id=self._keys.next_id(Refund),
                order_id=order.id,
                return_id=return_id,
                payment_id=payment.id,
                amount=amount,
                reason=reason,
                refunded_at=refunded_at,
            )
        )

    def _return_plan(self, day: _AfterSalesDay, order: Order) -> _ReturnPlan:
        """The whole return of an order, re-derived identically on every day it matters."""
        items = day.items[order.id]
        ranked = sorted(items, key=lambda item: self._fraction(item.id, LINE_RANK_PURPOSE, _ORDER_ITEM))
        returned = ranked[: 1 + int(self._fraction(order.id, LINE_COUNT_PURPOSE) * len(items))]
        quantities = {
            item.id: 1 + int(self._fraction(item.id, LINE_QUANTITY_PURPOSE, _ORDER_ITEM) * item.quantity)
            for item in sorted(returned, key=lambda item: item.id)
        }
        if any(item.variant_id in day.footwear_variants for item in returned):
            reason = ReturnReason.SIZE
        else:
            reason = _REASONS[bisect(_REASON_CUM_WEIGHTS, self._fraction(order.id, REASON_PURPOSE))]
        requested_at = self._requested_at(order)
        return _ReturnPlan(
            requested_at=requested_at,
            reason=reason,
            quantities=quantities,
            rejected=self._fraction(order.id, REJECTION_PURPOSE) < REJECTION_RATE,
            decided_at=requested_at + self._days(order.id, DECISION_DELAY_PURPOSE, DECISION_DELAY_DAYS),
            received_at=requested_at + self._days(order.id, RECEIPT_DELAY_PURPOSE, RECEIPT_DELAY_DAYS),
        )

    def _requested_at(self, order: Order) -> datetime:
        """A delivered order's updated_at is its delivery time."""
        return order.updated_at + self._days(order.id, REQUEST_DELAY_PURPOSE, REQUEST_DELAY_DAYS)

    def _days(self, order_id: int, purpose: str, days: tuple[int, int]) -> timedelta:
        low, high = days
        return timedelta(days=low + int(self._fraction(order_id, purpose) * (high - low + 1)))

    def _fraction(self, entity_id: int, purpose: str, kind: str = _ORDER) -> float:
        return self._random.entity_fraction(kind, entity_id, purpose)


@dataclass(frozen=True, slots=True)
class _ReturnPlan:
    requested_at: datetime
    reason: ReturnReason
    quantities: dict[int, int]  # order item id -> returned quantity
    rejected: bool
    decided_at: datetime  # approved or rejected
    received_at: datetime  # only if approved


class _AfterSalesDay:
    """The day's window and the snapshot's delivered orders indexed for lookup."""

    def __init__(self, snapshot: StoreSnapshot, changes: DayChanges, start: datetime, end: datetime) -> None:
        self.changes = changes
        self.orders = {order.id: order for order in snapshot.recently_delivered_orders}
        self.items: dict[int, list[OrderItem]] = {}
        for item in snapshot.recently_delivered_items:
            self.items.setdefault(item.order_id, []).append(item)
        self.payments: dict[int, list[Payment]] = {}
        for payment in snapshot.recently_delivered_payments:
            self.payments.setdefault(payment.order_id, []).append(payment)
        self.footwear_variants = {
            v.id for v in snapshot.variants if snapshot.product_category_l1.get(v.product_id) == FOOTWEAR
        }
        self._start = start
        self._end = end

    def includes(self, instant: datetime) -> bool:
        return self._start <= instant < self._end

    def has_footwear(self, order_id: int) -> bool:
        return any(item.variant_id in self.footwear_variants for item in self.items[order_id])
