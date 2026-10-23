from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import AwareDatetime

from sherpa_generator.records.base import Money, RawRecord

# Enum values are the literals of Spec A §4.1.


class AddressType(StrEnum):
    SHIPPING = "shipping"
    BILLING = "billing"


class OrderStatus(StrEnum):
    PLACED = "placed"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class SalesChannel(StrEnum):
    WEB = "web"
    APP = "app"


class PaymentMethod(StrEnum):
    CARD = "card"
    PAYPAL = "paypal"
    GIFT_CARD = "gift_card"


class PaymentStatus(StrEnum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"


class PromotionType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"
    FREE_SHIPPING = "free_shipping"


class ReturnReason(StrEnum):
    SIZE = "size"
    DAMAGED = "damaged"
    NOT_AS_DESCRIBED = "not_as_described"
    CHANGED_MIND = "changed_mind"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    RECEIVED = "received"
    REJECTED = "rejected"


class RefundReason(StrEnum):
    RETURN = "return"
    CANCELLATION = "cancellation"
    GOODWILL = "goodwill"


class Customer(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.customers"

    id: int
    email: str
    first_name: str
    last_name: str
    country_code: str
    marketing_opt_in: bool
    is_test_account: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class Address(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.addresses"

    id: int
    customer_id: int
    type: AddressType
    country_code: str
    city: str
    postal_code: str
    is_default: bool


class Category(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.categories"

    id: int
    parent_id: int | None = None
    name: str


class Product(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.products"

    id: int
    category_id: int
    name: str
    brand: str
    is_active: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProductVariant(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.product_variants"

    id: int
    product_id: int
    sku: str
    size: str
    color: str
    cost_price: Money
    list_price: Money
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Order(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.orders"

    id: int
    order_number: str
    customer_id: int
    shipping_address_id: int
    status: OrderStatus
    subtotal: Money
    discount_total: Money
    shipping_amount: Money
    tax_amount: Money
    order_total: Money
    sales_channel: SalesChannel
    placed_at: AwareDatetime
    updated_at: AwareDatetime


class OrderItem(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.order_items"

    id: int
    order_id: int
    variant_id: int
    quantity: int
    unit_price: Money


class OrderStatusChange(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.order_status_history"

    id: int
    order_id: int
    status: OrderStatus
    changed_at: AwareDatetime


class Payment(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.payments"

    id: int
    order_id: int
    method: PaymentMethod
    amount: Money
    status: PaymentStatus
    created_at: AwareDatetime


class Shipment(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.shipments"

    id: int
    order_id: int
    carrier: str
    shipping_cost: Money
    shipped_at: AwareDatetime
    delivered_at: AwareDatetime | None = None


class Promotion(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.promotions"

    id: int
    name: str
    type: PromotionType
    value: Money
    min_order_value: Money
    campaign_id: int | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class CouponCode(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.coupon_codes"

    id: int
    promotion_id: int
    code: str
    max_redemptions: int | None = None
    is_single_use: bool


class OrderDiscount(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.order_discounts"

    id: int
    order_id: int
    promotion_id: int
    coupon_code_id: int | None = None
    amount: Money


class Return(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.returns"

    id: int
    order_id: int
    reason: ReturnReason
    status: ReturnStatus
    requested_at: AwareDatetime
    updated_at: AwareDatetime


class ReturnItem(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.return_items"

    id: int
    return_id: int
    order_item_id: int
    quantity: int
    is_restockable: bool


class Refund(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.refunds"

    id: int
    order_id: int
    return_id: int | None = None
    payment_id: int
    amount: Money
    reason: RefundReason
    refunded_at: AwareDatetime
