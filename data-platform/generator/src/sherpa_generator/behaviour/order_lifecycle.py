from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import accumulate
from typing import TYPE_CHECKING, Final

from sherpa_generator.records import (
    Address,
    AddressType,
    CouponCode,
    EventType,
    Money,
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
    Shipment,
    TrackedEvent,
)
from sherpa_generator.records.base import MONEY_QUANTUM

if TYPE_CHECKING:
    import random
    from collections.abc import Sequence
    from datetime import datetime

    from sherpa_generator.behaviour.visitor_traffic import PurchaseIntent
    from sherpa_generator.simulation.calendar import SimulationCalendar
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
    from sherpa_generator.simulation.realism_rules import RealismRules
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

SHIPPING_FEE: Final = Decimal("4.95")  # Spec A §5.2 step 5
FREE_SHIPPING_THRESHOLD: Final = Decimal("75")  # Spec A §5.2 step 5: on subtotal - discount_total
TAX_RATE: Final = Decimal("0.20")  # Spec A §5.2 step 5: on subtotal - discount_total

STREAM: Final = "orders"
LINE_COUNT_WEIGHTS: Final = {1: 0.50, 2: 0.30, 3: 0.15, 4: 0.05}  # Spec A §5.2 step 5: 1-4 lines
SINGLE_QUANTITY_RATE: Final = 0.85
MULTI_QUANTITY: Final = (2, 3)
DISCOUNTED_ORDER_RATE: Final = 0.25  # Spec A §5.2 step 5
PAYMENT_METHOD_WEIGHTS: Final = {PaymentMethod.CARD: 0.70, PaymentMethod.PAYPAL: 0.25, PaymentMethod.GIFT_CARD: 0.05}
SPLIT_GIFT_CARD_PERCENT: Final = (20, 80)  # R7: the gift card part of a split payment
NEVER_PAID_RATE: Final = 0.02  # Spec A §5.2 step 6
PAID_DELAY_SECONDS: Final = (60, 1800)  # paid 1-30 min after placed, and always the same day (Spec A §5.2 step 6)
UNPAID_CANCELLATION_DELAY: Final = timedelta(days=1)  # Spec A §5.2 step 6: never paid -> cancelled next day
PAID_CANCELLATION_RATE: Final = 0.03  # Spec A §5.2 step 6: of paid orders, before shipping
PAID_CANCELLATION_DELAY: Final = timedelta(days=1)
SHIPPING_DELAY_DAYS: Final = (1, 3)  # Spec A §5.2 step 6
DELIVERY_DELAY_DAYS: Final = (2, 5)  # Spec A §5.2 step 6
CARRIERS: Final = ("DHL", "UPS", "DPD", "GLS")
CARRIER_COST_CENTS: Final = (350, 800)
# entity_fraction purposes of the decisions fixed per order
PAID_CANCELLATION_PURPOSE: Final = "paid_cancellation"
SHIPPING_DELAY_PURPOSE: Final = "shipping_delay"
DELIVERY_DELAY_PURPOSE: Final = "delivery_delay"
CARRIER_PURPOSE: Final = "carrier"
CARRIER_COST_PURPOSE: Final = "carrier_cost"
_ORDER: Final = "order"
_LINE_COUNTS: Final = tuple(LINE_COUNT_WEIGHTS)
_LINE_CUM_WEIGHTS: Final = tuple(accumulate(LINE_COUNT_WEIGHTS.values()))
_METHODS: Final = tuple(PAYMENT_METHOD_WEIGHTS)
_METHOD_CUM_WEIGHTS: Final = tuple(accumulate(PAYMENT_METHOD_WEIGHTS.values()))
_HUNDRED: Final = Decimal(100)
_CENTS_EXPONENT: Final = -2


class OrderPricing:
    """The order arithmetic of Spec A §4.1 and §5.2 step 5. Pure, so returns can reuse it for refunds."""

    def subtotal(self, items: Sequence[OrderItem]) -> Money:
        return _cents(sum((item.unit_price * item.quantity for item in items), Decimal(0)))

    def discount_amount(self, promotion: Promotion, subtotal: Money) -> Money:
        match promotion.type:
            case PromotionType.PERCENT:
                return _cents(subtotal * promotion.value / _HUNDRED)
            case PromotionType.FIXED:
                return min(promotion.value, subtotal)
            case PromotionType.FREE_SHIPPING:
                return _cents(Decimal(0))

    def shipping_amount(self, subtotal: Money, discount_total: Money, *, free_shipping: bool) -> Money:
        if free_shipping or subtotal - discount_total >= FREE_SHIPPING_THRESHOLD:
            return _cents(Decimal(0))

        return SHIPPING_FEE

    def tax_amount(self, subtotal: Money, discount_total: Money) -> Money:
        return _cents((subtotal - discount_total) * TAX_RATE)

    def order_total(self, subtotal: Money, discount_total: Money, *, free_shipping: bool) -> Money:
        shipping = self.shipping_amount(subtotal, discount_total, free_shipping=free_shipping)
        return subtotal - discount_total + shipping + self.tax_amount(subtotal, discount_total)

    def allocate_discount(self, line_gross: Sequence[Money], discount: Money) -> list[Money]:
        """Pro rata over the lines; the rounding remainder goes to the largest line, so the parts sum exactly."""
        if not line_gross:
            return []

        total = sum(line_gross, Decimal(0))
        parts = [_cents(discount * line / total) for line in line_gross]
        largest = max(range(len(line_gross)), key=line_gross.__getitem__)
        parts[largest] += discount - sum(parts, Decimal(0))
        return parts


class OrderLifecycleSimulator:
    """Spec A §5.2 steps 5-6: orders from the day's purchase intents, then the progression of open orders.

    Placement (lines, discounts, payment) draws from the day's stream and is paid the same day. Later transitions are
    fixed per order by `entity_fraction` and written on the day they fall on, so no timestamp lies beyond the day.
    """

    def __init__(
        self,
        random: DeterministicRandom,
        keys: PrimaryKeyAllocator,
        rules: RealismRules,
        calendar: SimulationCalendar,
    ) -> None:
        self._random = random
        self._keys = keys
        self._rules = rules
        self._calendar = calendar
        self._pricing = OrderPricing()

    def place_orders(self, snapshot: StoreSnapshot, changes: DayChanges, intents: list[PurchaseIntent]) -> list[Order]:
        """Orders with lines, discounts, status history and payments; web intents tag their purchase event."""
        variants = [v for v in snapshot.variants if v.id in snapshot.variant_popularity]
        day = _PlacementDay(
            changes=changes,
            rng=self._random.for_day(snapshot.day, STREAM),
            end=self._calendar.day_bounds(snapshot.day)[1],
            variants=variants,
            variants_by_id={v.id: v for v in variants},
            variant_cum_weights=list(accumulate(snapshot.variant_popularity[v.id] for v in variants)),
            promotions=snapshot.active_promotions,
            coupons=snapshot.coupon_codes,
            shipping_address_ids={**snapshot.shipping_address_ids, **self._new_shipping_address_ids(changes)},
            purchase_events=self._untagged_purchase_events(changes),
        )
        return [self._place(day, intent) for intent in intents]

    def progress_orders(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        """Status transitions of open orders that fall on this day, with their shipments and cancellation refunds."""
        end = self._calendar.day_bounds(snapshot.day)[1]
        shipments = {shipment.order_id: shipment for shipment in snapshot.open_shipments}
        captured: dict[int, list[Payment]] = {}
        for payment in snapshot.captured_payments:
            captured.setdefault(payment.order_id, []).append(payment)

        deliveries: list[tuple[Shipment, datetime]] = []
        for order in snapshot.open_orders:
            match order.status:
                case OrderStatus.PLACED:
                    self._transition(changes, order, OrderStatus.CANCELLED, UNPAID_CANCELLATION_DELAY, end)
                case OrderStatus.PAID if self._fraction(order, PAID_CANCELLATION_PURPOSE) < PAID_CANCELLATION_RATE:
                    if at := self._transition(changes, order, OrderStatus.CANCELLED, PAID_CANCELLATION_DELAY, end):
                        self._refund_cancellation(changes, captured[order.id], at)
                case OrderStatus.PAID:
                    delay = self._delay(order, SHIPPING_DELAY_PURPOSE, SHIPPING_DELAY_DAYS)
                    if at := self._transition(changes, order, OrderStatus.SHIPPED, delay, end):
                        self._ship(changes, order, at)
                case OrderStatus.SHIPPED:
                    delay = self._delay(order, DELIVERY_DELAY_PURPOSE, DELIVERY_DELAY_DAYS)
                    if at := self._transition(changes, order, OrderStatus.DELIVERED, delay, end):
                        deliveries.append((shipments[order.id], at))

        # After the order updates, so the writer batches each update shape into one statement.
        for shipment, delivered_at in deliveries:
            changes.update(Shipment, shipment.id, delivered_at=delivered_at)

    def _place(self, day: _PlacementDay, intent: PurchaseIntent) -> Order:
        order_id = self._keys.next_id(Order)
        placed_at = intent.purchased_at
        items = self._items(day, order_id, intent.variant_id)
        subtotal = self._pricing.subtotal(items)
        discounts = self._discounts(day, order_id, placed_at, subtotal)
        discount_total = sum((discount.amount for _, discount in discounts), Decimal(0))
        free_shipping = any(promotion.type == PromotionType.FREE_SHIPPING for promotion, _ in discounts)
        paid_at = None if day.rng.random() < NEVER_PAID_RATE else self._paid_at(day, placed_at)
        order = Order(
            id=order_id,
            order_number=f"SS-{order_id:06d}",
            customer_id=intent.customer_id,
            shipping_address_id=day.shipping_address_ids[intent.customer_id],
            status=OrderStatus.PLACED if paid_at is None else OrderStatus.PAID,
            subtotal=subtotal,
            discount_total=discount_total,
            shipping_amount=self._pricing.shipping_amount(subtotal, discount_total, free_shipping=free_shipping),
            tax_amount=self._pricing.tax_amount(subtotal, discount_total),
            order_total=self._pricing.order_total(subtotal, discount_total, free_shipping=free_shipping),
            sales_channel=intent.sales_channel,
            placed_at=placed_at,
            updated_at=placed_at if paid_at is None else paid_at,
        )
        day.changes.insert(order)
        for record in (*items, *(discount for _, discount in discounts)):
            day.changes.insert(record)
        self._insert_status_change(day.changes, order_id, OrderStatus.PLACED, placed_at)
        if paid_at is not None:
            self._insert_status_change(day.changes, order_id, OrderStatus.PAID, paid_at)
        self._insert_payments(day, order, paid_at)
        if intent.purchase_event_id is not None:
            self._tag_purchase_event(day, intent.purchase_event_id, order)

        return order

    def _items(self, day: _PlacementDay, order_id: int, carted_variant_id: int | None) -> list[OrderItem]:
        """1-4 distinct variants at today's list price: a web order's carted variant first, the rest by popularity."""
        count = min(day.rng.choices(_LINE_COUNTS, cum_weights=_LINE_CUM_WEIGHTS)[0], len(day.variants))
        chosen: dict[int, ProductVariant] = {}
        if carted_variant_id is not None:
            chosen[carted_variant_id] = day.variants_by_id[carted_variant_id]
        while len(chosen) < count:
            variant = day.rng.choices(day.variants, cum_weights=day.variant_cum_weights)[0]
            chosen.setdefault(variant.id, variant)

        return [
            OrderItem(
                id=self._keys.next_id(OrderItem),
                order_id=order_id,
                variant_id=variant.id,
                quantity=1 if day.rng.random() < SINGLE_QUANTITY_RATE else day.rng.randint(*MULTI_QUANTITY),
                unit_price=variant.list_price,
            )
            for variant in chosen.values()
        ]

    def _discounts(
        self, day: _PlacementDay, order_id: int, placed_at: datetime, subtotal: Money
    ) -> list[tuple[Promotion, OrderDiscount]]:
        """One eligible promotion for a quarter of the orders; R8 adds a coupon of another one on top."""
        if day.rng.random() >= DISCOUNTED_ORDER_RATE:
            return []

        eligible = {
            p.id: p for p in day.promotions if p.starts_at <= placed_at < p.ends_at and subtotal >= p.min_order_value
        }
        if not eligible:
            return []

        promotion = day.rng.choice(list(eligible.values()))
        applied: list[tuple[Promotion, CouponCode | None]] = [(promotion, None)]
        if day.rng.random() < self._rules.MULTI_DISCOUNT_ORDER_RATE / DISCOUNTED_ORDER_RATE:
            coupons = [c for c in day.coupons if c.promotion_id in eligible and c.promotion_id != promotion.id]
            if coupons:
                extra = day.rng.choice(coupons)
                applied.append((eligible[extra.promotion_id], extra))

        discounts: list[tuple[Promotion, OrderDiscount]] = []
        remaining = subtotal
        for applied_promotion, coupon in applied:
            amount = self._pricing.discount_amount(applied_promotion, remaining)  # stacked discounts never pass 100 %
            remaining -= amount
            discount = OrderDiscount(
                id=self._keys.next_id(OrderDiscount),
                order_id=order_id,
                promotion_id=applied_promotion.id,
                coupon_code_id=coupon.id if coupon else None,
                amount=amount,
            )
            discounts.append((applied_promotion, discount))
        return discounts

    def _paid_at(self, day: _PlacementDay, placed_at: datetime) -> datetime:
        """1-30 minutes after placement; late-evening orders get a shorter delay instead of one past midnight."""
        seconds_left = max(int((day.end - placed_at).total_seconds()) - 1, 0)
        latest = min(PAID_DELAY_SECONDS[1], seconds_left)
        earliest = min(PAID_DELAY_SECONDS[0], latest)
        return placed_at + timedelta(seconds=day.rng.randint(earliest, latest))

    def _insert_payments(self, day: _PlacementDay, order: Order, paid_at: datetime | None) -> None:
        """An authorization only for never-paid orders; otherwise R7's failed first attempt and split payments."""
        method = day.rng.choices(_METHODS, cum_weights=_METHOD_CUM_WEIGHTS)[0]
        if paid_at is None:
            self._insert_payment(
                day.changes, order.id, method, order.order_total, PaymentStatus.AUTHORIZED, order.placed_at
            )
            return

        parts = [(method, order.order_total)]
        if day.rng.random() < self._rules.SPLIT_PAYMENT_RATE:
            gift_card = _cents(order.order_total * day.rng.randint(*SPLIT_GIFT_CARD_PERCENT) / _HUNDRED)
            parts = [(PaymentMethod.GIFT_CARD, gift_card), (PaymentMethod.CARD, order.order_total - gift_card)]
        if day.rng.random() < self._rules.FAILED_PAYMENT_RATE:
            failed_method, failed_amount = parts[-1]
            self._insert_payment(
                day.changes, order.id, failed_method, failed_amount, PaymentStatus.FAILED, order.placed_at
            )
        for part_method, amount in parts:
            self._insert_payment(day.changes, order.id, part_method, amount, PaymentStatus.CAPTURED, paid_at)

    def _insert_payment(
        self,
        changes: DayChanges,
        order_id: int,
        method: PaymentMethod,
        amount: Money,
        status: PaymentStatus,
        created_at: datetime,
    ) -> None:
        changes.insert(
            Payment(
                id=self._keys.next_id(Payment),
                order_id=order_id,
                method=method,
                amount=amount,
                status=status,
                created_at=created_at,
            )
        )

    def _tag_purchase_event(self, day: _PlacementDay, event_id: str, order: Order) -> None:
        """Records are frozen, so the purchase event (and any R1 copy of it) is swapped for one carrying the order.

        Its cart_value becomes the order subtotal, so the event agrees with the order it created.
        """
        for event in day.purchase_events[event_id]:
            properties = event.properties.model_copy(update={"order_id": order.id, "cart_value": order.subtotal})
            day.changes.replace(event, event.model_copy(update={"properties": properties}))

    def _transition(
        self, changes: DayChanges, order: Order, status: OrderStatus, delay: timedelta, end: datetime
    ) -> datetime | None:
        """Apply the transition due `delay` after the order's last one if it falls before `end`; returns its time."""
        at = order.updated_at + delay
        if at >= end:
            return None

        self._insert_status_change(changes, order.id, status, at)
        changes.update(Order, order.id, status=status, updated_at=at)
        return at

    def _insert_status_change(self, changes: DayChanges, order_id: int, status: OrderStatus, at: datetime) -> None:
        changes.insert(
            OrderStatusChange(id=self._keys.next_id(OrderStatusChange), order_id=order_id, status=status, changed_at=at)
        )

    def _ship(self, changes: DayChanges, order: Order, shipped_at: datetime) -> None:
        low, high = CARRIER_COST_CENTS
        cost_cents = low + int(self._fraction(order, CARRIER_COST_PURPOSE) * (high - low + 1))
        changes.insert(
            Shipment(
                id=self._keys.next_id(Shipment),
                order_id=order.id,
                carrier=CARRIERS[int(self._fraction(order, CARRIER_PURPOSE) * len(CARRIERS))],
                shipping_cost=Decimal(cost_cents).scaleb(_CENTS_EXPONENT),
                shipped_at=shipped_at,
            )
        )

    def _refund_cancellation(self, changes: DayChanges, payments: list[Payment], refunded_at: datetime) -> None:
        """Spec A §5.2 step 7: the full captured amount, booked against the largest captured payment."""
        largest = max(payments, key=lambda payment: payment.amount)
        changes.insert(
            Refund(
                id=self._keys.next_id(Refund),
                order_id=largest.order_id,
                payment_id=largest.id,
                amount=sum((payment.amount for payment in payments), Decimal(0)),
                reason=RefundReason.CANCELLATION,
                refunded_at=refunded_at,
            )
        )

    def _delay(self, order: Order, purpose: str, days: tuple[int, int]) -> timedelta:
        low, high = days
        return timedelta(days=low + int(self._fraction(order, purpose) * (high - low + 1)))

    def _fraction(self, order: Order, purpose: str) -> float:
        return self._random.entity_fraction(_ORDER, order.id, purpose)

    @staticmethod
    def _new_shipping_address_ids(changes: DayChanges) -> dict[int, int]:
        """Default shipping addresses of today's signups, which the snapshot does not hold yet."""
        return {
            address.customer_id: address.id
            for address in changes.inserted(Address)
            if address.type == AddressType.SHIPPING and address.is_default
        }

    @staticmethod
    def _untagged_purchase_events(changes: DayChanges) -> dict[str, list[TrackedEvent]]:
        """Today's purchase events without an order; flushed late events were tagged on the day they occurred."""
        events: dict[str, list[TrackedEvent]] = {}
        for event in (*changes.inserted(TrackedEvent), *changes.inserted(PendingEvent)):
            if event.event_type == EventType.PURCHASE and event.properties.order_id is None:
                events.setdefault(event.event_id, []).append(event)
        return events


@dataclass(frozen=True, slots=True)
class _PlacementDay:
    """What placing one day's orders needs, gathered once per day."""

    changes: DayChanges
    rng: random.Random
    end: datetime
    variants: list[ProductVariant]
    variants_by_id: dict[int, ProductVariant]
    variant_cum_weights: list[float]
    promotions: list[Promotion]
    coupons: list[CouponCode]
    shipping_address_ids: dict[int, int]
    purchase_events: dict[str, list[TrackedEvent]]  # untagged purchase events (and R1 copies) by event_id


def _cents(value: Decimal) -> Money:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
