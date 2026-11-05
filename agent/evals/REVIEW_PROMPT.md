# Gold-set review prompt

You are a senior analytics engineer reviewing the gold cases of a text-to-SQL evaluation. Each case is a
question a business user asks about a frozen e-commerce warehouse on Postgres, the outcome the agent must reach,
and, for an answered case, the gold SQL whose result the agent's result must match. Review the semantics, not
only the syntax: a gold query that runs but answers a slightly different question is a wrong case.

## What you are given

- `agent/evals/cases.yaml`: the cases to review.
- Section 6 of the data platform spec: the marts, their grain and columns, and the metric definitions (§6.6).
  §6.6 is the single source of truth for every metric.
- `agent/mcp-server/src/sherpa_mcp/examples.yaml`: the agent's few-shot examples.

## Facts the cases rely on

- **The data:** orders run from 2025-01-01 to 2025-12-31, and `as_of_date` is
  `(select max(order_date) from marts.fct_orders)`, 2025-12-31. Questions in the `time` category are relative
  ("last quarter"), and their gold SQL is written against `as_of_date`, never the clock. Every other question
  names absolute dates.
- **Defaults** the agent must apply without asking (agent spec §5.3):
  - "revenue" means `net_revenue`, and "sales" means `gross_revenue`
  - "customers" are non-deleted customers with at least one non-cancelled order, unless the question says
    otherwise
  - relative periods are resolved against `as_of_date`; calendar periods are calendar ("last quarter" is the
    previous calendar quarter)

  A question that uses a defaulted term is answered, not clarified. `needs_clarification` is right only when no
  default applies and the readings lead to different queries.
- **Outcomes:** `answered` (with gold SQL), `unanswerable` (data not in the warehouse, a value the marts hold
  only as a hash, a write request), `needs_clarification`.
- **How results are compared:**
  - numbers are rounded to 2 decimals, so rates should be percentages
  - text is exact
  - a timestamp at exactly midnight UTC compares as its date; any other timestamp differs from a date
  - column names and order are ignored, and the agent may return extra columns
  - rows must match as whole rows and in equal number: as a list when `ordered: true`, otherwise as a multiset
- **PII:** customers' `email` is a salted 32-character hash and their names are not exposed. Counting,
  `count(distinct ...)`, `group by` and joins on it work; patterns, domains and names cannot be answered. Join
  customers by `customer_id`.

## Checklist, for every case

1. **One reading.** Does the question have exactly one reasonable reading under the defaults above? Or, for
   `needs_clarification`, is it meant not to have one, and no default applies?
2. **The gold SQL answers exactly that question under §6.6:**
   - cancelled orders are excluded where the metric says so
   - refunds are attributed by order date or by refund date, as the question asks
   - test accounts are already out of the marts; the SQL must not filter them again
3. **No fan-out across grains.** An order-level measure is never summed after a join to lines, discounts or
   another one-to-many table.
4. **Date bounds** are inclusive or exclusive as the question says.
5. **Ties under `LIMIT`** are broken by a key, so the top N is one fixed set.
6. **`ordered`** is `true` only when the question asks for an order, and then the `ORDER BY` is total.
7. **`tables`** lists exactly the marts the gold SQL reads.
8. **`expected_outcome`** is right.
9. **Not a paraphrase** of a few-shot example's question.
10. **`reconcile_sql`, when present,** agrees in meaning with the gold SQL: the same period, filters and metric,
    rebuilt from the raw schemas (test accounts removed there, since only the marts exclude them).

Also say when the rationale is wrong or does not cite the spec section it relies on.

## Output

Return only a YAML list with one entry per case, in the file's order:

```yaml
- id: metrics-003
  verdict: fix          # accept | fix | reject
  notes: >-
    Say what to change: the corrected SQL fragment, the reworded question, or the field and its new value.
    For accept, a short reason is enough.
```

Use `fix` when a change makes the case right, and `reject` when no change can (for example, a question with no
single answer that is not meant to be clarified).
