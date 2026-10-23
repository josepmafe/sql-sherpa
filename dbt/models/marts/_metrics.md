{#-
    Metric definitions, written once and reused in column descriptions with doc('metric_<name>').
-#}
{% docs metric_gross_revenue %}
Gross revenue: Σ quantity × unit_price over the order lines, in EUR. Net of tax, excludes shipping,
before discounts. As a metric it is summed over non-cancelled orders (`not is_cancelled`).
{% enddocs %}

{% docs metric_discount_amount %}
Discount amount: Σ of the order's discounts in EUR (promotions and coupons), allocated to the order
lines pro-rata to their gross amount. Free-shipping promotions waive shipping and add 0 here.
{% enddocs %}

{% docs metric_refunded_revenue %}
Refunded revenue: the revenue given back by refunds, in EUR. For refunds with reason `return` or
`goodwill` it is the refunded amount without its tax, amount / (1 + tax_rate) with tax_rate 0.20;
these refunds never include shipping. Refunds with reason `cancellation` count as 0: the cancelled
order already contributes no revenue, and the refund only reverses the payment. In `fct_orders` it is
attributed to the order's date, not to the date of the refund.
{% enddocs %}

{% docs metric_net_revenue %}
Net revenue: gross_revenue − discount_amount − refunded_revenue, in EUR, over non-cancelled orders.
It is 0 for a cancelled order. This is what "revenue" means unless stated otherwise.
{% enddocs %}

{% docs metric_cogs %}
Cost of goods sold (COGS): Σ quantity × unit cost at the time of sale, in EUR. Returned COGS is the
restockable returned quantity × unit cost at the time of sale: those units go back into stock, so
their cost is recovered. Both are 0 for a cancelled order.
{% enddocs %}

{% docs metric_gross_margin %}
Gross margin: net_revenue − (cogs − returned_cogs), in EUR. It is 0 for a cancelled order.
{% enddocs %}

{% docs metric_aov %}
Average order value (AOV): Σ net_revenue / count of non-cancelled orders, in EUR.
{% enddocs %}

{% docs metric_refund_rate %}
Refund rate: Σ refunded_revenue / Σ gross_revenue over the same orders, as a fraction.
{% enddocs %}

{% docs cancelled_orders %}
Cancelled orders are kept and flagged with is_cancelled. Their amount columns describe the order as
placed and keep their values: gross_revenue, discount_amount, shipping_amount, tax_amount and
order_total, and on the lines gross_amount, allocated_discount and net_amount. Their outcome columns
are 0, because nothing was sold: net_revenue, cogs, returned_cogs and gross_margin, and the line cogs.
Metrics over non-cancelled orders filter on `not is_cancelled`.
{% enddocs %}
