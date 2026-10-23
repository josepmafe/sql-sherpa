# Extending the source data model

How to change what the generator writes and carry the change through to dbt: which files to touch, in which order, and which tests prove it. Every example below was applied on a scratch copy and its tests run; outputs are real.

## Before you start

**The spec comes first.** The source data model (tables, columns, enum values), the daily volumes and the realism rules are defined in the data platform spec (`docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`, §4 and §5). A change to any of them is a spec change: agree it with the owner, then update the spec in the same branch as the code.

**How a simulated day works.** `Simulator.run` (`generator/src/sherpa_generator/simulation/simulator.py`) simulates one day at a time, each in its own transaction:

1. `StoreSnapshotLoader` reads the shop as it stands at the start of the day into a frozen `StoreSnapshot`.
2. `_DaySimulator.simulate` calls the behaviour simulators in `generator/src/sherpa_generator/behaviour/` in a fixed order. Each one reads the snapshot and queues inserts and updates in a `DayChanges`; none of them touches the database.
3. `RecordWriter` writes the `DayChanges` with `COPY` and saves the simulation state (last day and primary-key counters) in the same transaction.

Records are frozen Pydantic models in `generator/src/sherpa_generator/records/`, one class per raw table. A record's field order is its table's column order, and `RawSchema.TABLE_DDL` (`generator/src/sherpa_generator/database/raw_schema.py`) holds the matching `CREATE TABLE`. The test `TestRawSchema::test_table_columns_match_records` keeps the two in sync.

**The commands you will use.** Every Python command goes through uv; `--env-file .env` gives the integration tests the database settings:

```bash
uv run --env-file .env pytest generator/tests/<path>   # some generator tests
make test                                              # every test except the slow ones
make lint                                              # ruff, ruff format, mypy, sqlfluff
make reset && uv run sherpa-generate generate --days 30 --scale 0.1   # fresh small dataset
make build                                             # dbt on the dev target
make docs-check                                        # every model and column described
```

The raw schema is created once, so a new column or table only appears after `make reset`. A small dataset is enough to check the dbt side; the last step of each change is a full `make reset && make backfill DAYS=365 && make build`.

## 1. Add a column to an existing table

Example: `customers.preferred_language`, the language a customer chose at signup, one of `de`, `en`, `es`, `fr`, `it`, `nl`.

**1. The record** (`generator/src/sherpa_generator/records/shop.py`). The values are a closed set, so add a `StrEnum`, and add the field where the column goes:

```python
class Language(StrEnum):
    DE = "de"
    EN = "en"
    ES = "es"
    FR = "fr"
    IT = "it"
    NL = "nl"


class Customer(RawRecord):
    ...
    country_code: str
    preferred_language: Language
    marketing_opt_in: bool
    ...
```

Export `Language` from `records/__init__.py` (import and `__all__`).

**2. The DDL** (`RawSchema.TABLE_DDL`), at the same position:

```sql
        country_code text,
        preferred_language text,
        marketing_opt_in boolean,
```

Raw tables are deliberately loose: enums are `text` and there are no foreign keys. If you skip this step, the sync test says exactly where they differ:

```
E             At index 5 diff: 'marketing_opt_in' != 'preferred_language'
...
FAILED generator/tests/database/test_raw_schema.py::TestRawSchema::test_table_columns_match_records
```

**3. The simulator that sets it.** Customers are created in `VisitorTrafficSimulator._signup` (`behaviour/visitor_traffic.py`). Derive the language from the visitor's locale:

```
            country_code=self._rules.noisy_country(session.country, day.rng),
            # Derived from the locale, not drawn: a new draw on the day's stream would shift every later draw.
            preferred_language=Language(LOCALE_BY_COUNTRY[session.country].split("_")[0]),
```

Deriving instead of drawing keeps every other row identical: note the row count of a 30-day run at scale 0.1 before the change and compare it after: it is the same (one run wrote 7,934 rows; your count depends on the day you run, because the window ends yesterday). A new `day.rng` draw is allowed, but it changes everything drawn after it that day.

**4. The test fixtures and a test.** Every `Customer(...)` must pass the new field, so add `preferred_language=Language.DE` to `make_customer` in `generator/tests/helpers.py`. Then test the rule in `generator/tests/behaviour/test_visitor_traffic.py`:

```python
    def test_signup_language_follows_the_signup_country(self) -> None:
        expected = {"DE": "de", "AT": "de", "FR": "fr", "ES": "es", "IT": "it", "NL": "nl", "BE": "nl", "GB": "en"}
        changes = DayChanges()

        outcome = _simulator().simulate_day(_snapshot(), changes)

        assert outcome.new_customers
        for customer in outcome.new_customers:
            country = "GB" if customer.country_code.upper() == "UK" else customer.country_code.upper()
            assert customer.preferred_language == expected[country]
```

```bash
uv run --env-file .env pytest generator/tests/behaviour/test_visitor_traffic.py generator/tests/database generator/tests/records -q
```

```
... passed in ...s
```

**5. The source YAML** (`dbt/models/staging/shop/_shop__sources.yml`), under `customers`:

```yaml
          - name: preferred_language
            description: ISO 639-1 code of the language the customer chose at signup.
```

**6. The staging model and its tests.** Select the column in `dbt/models/staging/shop/stg_shop__customers.sql`:

```sql
        source.last_name,
        source.preferred_language,
        source.marketing_opt_in,
```

and document and test it in `dbt/models/staging/shop/_shop__models.yml`. An enum gets an `accepted_values` test with exactly the `StrEnum` values:

```yaml
      - name: preferred_language
        description: ISO 639-1 code of the language the customer chose at signup.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [de, en, es, fr, it, nl]
```

Without the YAML entry, `make docs-check` fails:

```
Missing descriptions:
  stg_shop__customers.preferred_language
```

**7. Regenerate and build:**

```bash
make reset && uv run sherpa-generate generate --days 30 --scale 0.1
make build
make docs-check
```

```
09:36:13  132 of 148 PASS accepted_values_stg_shop__customers_preferred_language__de__en__es__fr__it__nl  [PASS in 0.08s]
09:36:13  136 of 148 PASS not_null_stg_shop__customers_preferred_language ................ [PASS in 0.05s]
09:36:13  Done. PASS=148 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=148
```

Finish with `make test`, `make lint` and `make reset && make backfill DAYS=365 && make build`.

## 2. Add a new table

Example: `raw_shop.wishlist_items`, the variants customers save to a wishlist.

**1. The record class** (`records/shop.py`), exported from `records/__init__.py`:

```python
class WishlistItem(RawRecord):
    TABLE: ClassVar[str] = "raw_shop.wishlist_items"

    id: int
    customer_id: int
    variant_id: int
    added_at: AwareDatetime
```

`TABLE` is how the generator finds the table. `KEY` defaults to `("id",)`; set it only for another key (`AdSpendDay` uses `("spend_date", "campaign_id")`).

**2. The DDL**, appended to the `raw_shop` tables in `RawSchema.TABLE_DDL`:

```sql
    CREATE TABLE IF NOT EXISTS raw_shop.wishlist_items (
        id bigint PRIMARY KEY,
        customer_id bigint,
        variant_id bigint,
        added_at timestamptz
    )
```

**3. The primary key.** Keys never come from Postgres sequences: `PrimaryKeyAllocator.next_id(WishlistItem)` hands out the next id, and its counters are saved per `TABLE` in `raw_meta.simulation_state.next_ids` with each day. A new table gets its counter on first use; there is nothing to register.

**4. The `StoreSnapshot` fields it needs** (`simulation/store_snapshot.py`). A simulator only sees the database through the snapshot. Customers and `variant_popularity` are already there; the existing wishlist items are not, and they are needed so a customer never saves the same variant twice:

```python
    open_returns: list[Return]  # requested or approved
    wishlist_items: list[WishlistItem]
```

and in `StoreSnapshotLoader.load`:

```
            wishlist_items=self._records(WishlistItem),
```

`_records` orders rows by key, so the snapshot is the same on every run. Load only what a simulator needs: everything in it is read once a day.

**5. The behaviour simulator** (`behaviour/wishlists.py`): a class that takes its collaborators in the constructor and has a `simulate_day(snapshot, changes)` method.

```python
STREAM: Final = "wishlists"
DAILY_SAVE_RATE: Final = 0.02  # share of customers who save a variant on a given day
SECONDS_PER_DAY: Final = 86_400


class WishlistSimulator:
    """Customers save variants to their wishlist, weighted by popularity; a variant is saved at most once."""

    def __init__(self, random: DeterministicRandom, keys: PrimaryKeyAllocator, calendar: SimulationCalendar) -> None:
        self._random = random
        self._keys = keys
        self._calendar = calendar

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        if not snapshot.variant_popularity:
            return

        rng = self._random.for_day(snapshot.day, STREAM)
        start, _ = self._calendar.day_bounds(snapshot.day)
        saved = {(item.customer_id, item.variant_id) for item in snapshot.wishlist_items}
        variant_ids = list(snapshot.variant_popularity)
        weights = list(snapshot.variant_popularity.values())
        for customer in snapshot.customers:
            if rng.random() >= DAILY_SAVE_RATE:
                continue

            variant_id = rng.choices(variant_ids, weights=weights)[0]
            if (customer.id, variant_id) in saved:
                continue

            saved.add((customer.id, variant_id))
            changes.insert(
                WishlistItem(
                    id=self._keys.next_id(WishlistItem),
                    customer_id=customer.id,
                    variant_id=variant_id,
                    added_at=start + timedelta(seconds=rng.randrange(SECONDS_PER_DAY)),
                )
            )
```

Its own `STREAM` name gives it a random sequence no other simulator consumes, so adding it leaves every other table unchanged: the 30-day run at scale 0.1 writes the row count you noted before the change plus the wishlist items.

**6. Wire it into the day** (`_DaySimulator` in `simulation/simulator.py`):

```python
        self._wishlists = WishlistSimulator(random, keys, calendar)
```

```python
        traffic = self._traffic.simulate_day(snapshot, changes)
        self._wishlists.simulate_day(snapshot, changes)
        self._crm.simulate_day(snapshot, changes)
```

Where it goes in the order matters in two ways:

- **Inputs.** Every simulator reads the start-of-day snapshot, so a later simulator only sees an earlier one's output when it is passed in explicitly. `place_orders` receives the `purchase_intents` returned by the traffic simulator, and `AdSpendSimulator` its `paid_sessions_by_campaign`. A simulator that needs today's signups or orders must come after the one that produces them and take that output as an argument.
- **Updates.** `RecordWriter` applies the queued updates in order, so a simulator that updates rows another one also updates must come after it. `CustomerDeletionSimulator` runs last, after the day's activity.

The wishlist simulator needs only the snapshot, so it sits with the browsing behaviour, right after the traffic. Its keys and random stream are its own, so the position does not change any other row.

**7. The tests.**

- Every test that builds a `StoreSnapshot` must pass the new field: add `"wishlist_items": []` to `_EMPTY_SNAPSHOT` in `generator/tests/helpers.py` and `wishlist_items=[]` to the `_snapshot` helpers of `test_visitor_traffic.py`, `test_order_lifecycle.py` and `test_crm_activity.py`.
- `test_every_record_type_has_a_table_and_unique_columns` (`generator/tests/records/test_records.py`) counts the record types: 22 becomes 23.
- A unit test per behaviour in `generator/tests/behaviour/test_wishlists.py`, with `make_snapshot` and `make_customer` from `helpers`: the rate and that every item falls inside the day, that a saved variant is never saved again, and that a shop without variants saves nothing.
- Nothing to add for determinism: `fingerprint` hashes every raw table, so `test_fresh_runs_are_identical` and `test_resume_equals_single_run` (`generator/tests/simulation/test_simulator.py`) cover the new table automatically.

```bash
uv run --env-file .env pytest generator/tests -q
```

```
... passed, 2 deselected in ...s
```

**8. dbt.** A source table entry in `_shop__sources.yml` with every column described, the staging model `dbt/models/staging/shop/stg_shop__wishlist_items.sql`:

```sql
with

source as (

    select * from {{ source('shop', 'wishlist_items') }}

),

renamed as (

    select
        id as wishlist_item_id,
        customer_id,
        variant_id,
        added_at
    from source

)

select * from renamed
```

and its entry in `_shop__models.yml`: `unique` and `not_null` on the key, `relationships` from `customer_id` to `stg_shop__customers` and from `variant_id` to `stg_shop__product_variants`, and a description on every column.

```bash
make reset && uv run sherpa-generate generate --days 30 --scale 0.1
make build
make docs-check
```

```
...
Done. PASS=N WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=N
```

If the table should be checked for freshness, add a `freshness` block and `loaded_at_field` to its source entry, as `orders` has.

## 3. Add or change an enum value

Example: a new payment method, `klarna`.

**1. The `StrEnum`** (`records/shop.py`):

```python
class PaymentMethod(StrEnum):
    CARD = "card"
    PAYPAL = "paypal"
    GIFT_CARD = "gift_card"
    KLARNA = "klarna"
```

**2. The generator weights** (`PAYMENT_METHOD_WEIGHTS` in `behaviour/order_lifecycle.py`). They must sum to 1:

```python
PAYMENT_METHOD_WEIGHTS: Final = {
    PaymentMethod.CARD: 0.65,
    PaymentMethod.PAYPAL: 0.25,
    PaymentMethod.GIFT_CARD: 0.05,
    PaymentMethod.KLARNA: 0.05,
}
```

The draw is still one `choices` call per order, so the other tables keep their rows; only the methods move. Split payments (R7) are always gift card plus card and do not use the weights.

**3. The staging `accepted_values` test.** It lists the allowed values, so the new data fails it until it is updated. With a 30-day run at scale 0.5 (at scale 0.1 a 5 % method may not show up at all):

```
09:37:34  80 of 146 FAIL 1 accepted_values_stg_shop__payments_method__card__paypal__gift_card  [FAIL 1 in 0.13s]
...
09:37:36  Done. PASS=145 WARN=0 ERROR=1 SKIP=0 NO-OP=0 REUSED=0 TOTAL=146
```

`FAIL 1` is one unexpected value. Add it in `_shop__models.yml`:

```yaml
      - name: method
        description: Payment method.
        data_tests:
          - accepted_values:
              arguments:
                values: [card, paypal, gift_card, klarna]
```

```
09:38:41  2 of 8 PASS accepted_values_stg_shop__payments_method__card__paypal__gift_card__klarna  [PASS in 0.14s]
09:38:41  Done. PASS=8 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=8
```

The generator tests that cover payments still pass (`uv run --env-file .env pytest generator/tests/behaviour/test_order_lifecycle.py generator/tests/behaviour/test_after_sales.py`: 51 passed). Renaming or removing a value works the same way, and also breaks every downstream model that filters on the old value; search `dbt/` for it.

## 4. Add or tune a realism rule

The realism rules R1–R12 are a fixed list (spec §4.4). Each one is paired with the dbt technique that has to handle it, in the spec's "dbt must…" column: dedupe in staging, normalise through a seed, sum only captured payments, and so on. A rule without such a job only adds noise. Keeping the generator simple is a project principle, so a new rule or knob needs the owner's agreement and a spec change first. Tuning the rate of an existing rule is a spec change too.

Where a rule lives:

- **Its rate** is a constant on `RealismRules` (`simulation/realism_rules.py`) with the rule number in a comment, for example `EMAIL_NOISE_RATE: Final = 0.10  # R3`. Behaviour simulators read the rates from there.
- **Its noise** is a method on `RealismRules` when the rule changes a value (`noisy_email`, `noisy_country`, `duplicate_events`, `late_delivery_delay`, `is_test_account`, `ad_clicks`), or lives in the simulator that owns the behaviour when it changes what happens (split and failed payments in `OrderLifecycleSimulator._insert_payments`, soft deletes in `CustomerDeletionSimulator`).
- **Its rate test** is in `generator/tests/simulation/test_realism_rules.py`: 20,000 draws with a seeded `random.Random`, asserted within ±20 % of the spec rate (`assert_rate`).
- **Its cleanup** is in staging: R3 is `lower(trim(email))` in `stg_shop__customers` and `stg_crm__leads`, R4 the left join to the `country_code_variants` seed, R1 the `row_number()` dedupe in `stg_events__events`, R5 the `is_test_account` flag in `stg_shop__customers`.

Example: tune R3 from 10 % to 15 %. Change the constant:

```python
    EMAIL_NOISE_RATE: Final = 0.15  # R3
```

The rate test still asserts the spec's old value and fails:

```
E         Obtained: 0.14895
E         Expected: 0.1 ± 0.02
...
FAILED generator/tests/simulation/test_realism_rules.py::TestRealismRules::test_email_noise_rate
1 failed, 18 passed in 3.33s
```

Update it to the new spec value (`assert_rate(noisy, 0.15)`) and run the rule tests and the simulators that use the rule:

```bash
uv run pytest generator/tests/simulation/test_realism_rules.py -q
uv run --env-file .env pytest generator/tests/behaviour/test_visitor_traffic.py generator/tests/behaviour/test_crm_activity.py -q
```

```
19 passed in 3.84s
22 passed in 21.12s
```

The staging cleanup needs no change, since `lower(trim(...))` handles any amount of noise. A new kind of noise needs new cleanup, and a dbt test that proves it (for example a `dbt_utils.expression_is_true` or an `accepted_values` test on the cleaned column), run with `make build` on regenerated data.

## 5. Change volumes or rates

The daily volumes and behaviour rates of spec §5.2 are module-level `Final` constants at the top of each behaviour module, each with the spec section in a comment:

| What | Constant | Module |
|---|---|---|
| Sessions per day | `BASE_DAILY_SESSIONS` (400) | `behaviour/visitor_traffic.py` |
| Signups per day | `BASE_DAILY_SIGNUPS` (12) | `behaviour/visitor_traffic.py` |
| Channel mix | `CHANNEL_WEIGHTS` | `behaviour/visitor_traffic.py` |
| Funnel | `PRODUCT_VIEW_RATE`, `ADD_TO_CART_RATE`, `BEGIN_CHECKOUT_RATE`, `PURCHASE_RATE` | `behaviour/visitor_traffic.py` |
| Returning visitors, new-visitor purchases | `RETURNING_VISITOR_SHARE`, `NEW_VISITOR_PURCHASE_RATE` | `behaviour/visitor_traffic.py` |
| Leads per day, email engagement | `BASE_DAILY_LEADS`, `OPEN_RATE`, `CLICK_RATE` | `behaviour/crm_activity.py` |
| Order lines, discounts, payment methods | `LINE_COUNT_WEIGHTS`, `DISCOUNTED_ORDER_RATE`, `PAYMENT_METHOD_WEIGHTS` | `behaviour/order_lifecycle.py` |
| Order lifecycle | `NEVER_PAID_RATE`, `PAID_CANCELLATION_RATE`, `SHIPPING_DELAY_DAYS`, `DELIVERY_DELAY_DAYS` | `behaviour/order_lifecycle.py` |
| Returns timing | `REQUEST_DELAY_DAYS`, `RECEIPT_DELAY_DAYS` | `behaviour/after_sales.py` |
| Seasonality | `WEEKEND_FACTOR`, `BLACK_FRIDAY_WEEK_FACTOR`, `DECEMBER_FACTOR`, `JANUARY_FACTOR` on `SimulationCalendar` | `simulation/calendar.py` |
| Customer value spread | `PROPENSITY_SIGMA` | `simulation/store_snapshot.py` |
| Rates of the realism rules | class constants of `RealismRules` | `simulation/realism_rules.py` |

The comments next to `RETURNING_VISITOR_SHARE`, `NEW_VISITOR_PURCHASE_RATE` and `BASE_DAILY_SIGNUPS` explain how they were tuned to land on the spec's conversion rate and yearly totals; volumes interact, so change one constant at a time. `--scale` multiplies every daily volume at run time and needs no code change.

The behaviour tests check the rates on a 30-day run at scale 0.2 (for example `test_session_conversion_rate`, `test_discount_share`, `test_app_share`). The yearly totals are checked by the slow full-year test, which simulates 365 days at scale 1.0 into a temporary database:

```bash
uv run --env-file .env pytest -m slow
```

The simulation takes as long as `make backfill DAYS=365` (2.5 minutes on a laptop), plus the test setup. It holds two tests:

- `test_full_year_volumes_and_runtime` asserts ~5,000 customers, ~8,000 orders, ~600,000 events and ~6,000 leads (each ±15 %) and a simulation runtime under 5 minutes (spec §5.2). With `-o log_cli=true --log-cli-level=INFO` it logs what it measured: `runtime <seconds> s, volumes {'customers': 4951, 'orders': 8090, 'events': 670844, 'leads': 5815}`. The runtime limit is sensitive to load: with other work running against the same Postgres it can fail the runtime assertion while every volume is within tolerance; rerun it on an idle machine before reading that as a regression.

  ```
  2 passed, 270 deselected in 161.62s (0:02:41)
  ```

- `test_top_buyers_hold_55_to_65_percent_of_revenue` asserts that the top 20 % of buyers (customers with at least one non-cancelled order) hold 55–65 % of that revenue (spec §4.4, a soft target). On the 365-day dataset above they hold 0.575. It is the check for `PROPENSITY_SIGMA` and the visitor purchase rates.

The expected result is `2 passed`, as in the output above.

## 6. Keep determinism intact

Given the same seed, start date, scale and day of the first run, two runs produce identical data, and a run resumed after a stop or a crash equals one long run. Every change must keep that:

- **All randomness comes from `DeterministicRandom`.** Use `for_day(day, STREAM)` with a stream name of your own for the day's draws and `faker_for_day(day, STREAM)` for names and addresses. Never use the `random` module's global functions, `uuid.uuid4()` or the wall clock inside a simulator.
- **Never use Python's built-in `hash()`**, and never iterate a set or dict of strings to decide anything: string hashes change with every process (`PYTHONHASHSEED`). Use `stable_int` for a stable integer and sort before iterating.
- **Keys come from `PrimaryKeyAllocator`**, never from Postgres sequences or `max(id) + 1`.
- **Decisions about an entity's future use `entity_fraction(kind, entity_id, purpose)`**, not the day's stream: when an order ships, whether a customer is deleted and when. A resumed run starts with fresh simulators, and `entity_fraction` gives the same answer for the same entity every time. See `CustomerDeletionSimulator` and the `*_PURPOSE` constants of `OrderLifecycleSimulator`.
- **State comes from the snapshot,** which is read from the database. A cache on a simulator is only safe for values that never change (like the deletion time per customer in `CustomerDeletionSimulator`).

The tests that prove it:

```bash
uv run --env-file .env pytest generator/tests/simulation/test_simulator.py generator/tests/simulation/test_deterministic_random.py -q
```

- `TestSimulator::test_fresh_runs_are_identical`: two fresh 10-day runs (seed 7, scale 0.1) have the same row count and row-level md5 in every raw table.
- `TestSimulator::test_resume_equals_single_run`: 4 days, then 6 more in a second run, equal the same 10 days in one run, with late events crossing the boundary.
- `TestSimulator::test_crash_mid_day_rolls_back_and_resume_matches`: a crash inside a day's transaction leaves the previous day intact, and resuming gives the same data.
- `TestDeterministicRandom::test_for_day_is_stable_across_processes` and `test_faker_output_is_independent_of_pythonhashseed`: the day's draws and Faker's output are the same in subprocesses with different `PYTHONHASHSEED` values. A simulator that calls a Faker method not in that test's `methods` tuple should add it there.

Breaking a rule shows up here. With `marketing_opt_in` drawn from the global `random` module instead of `day.rng`:

```
FAILED generator/tests/simulation/test_simulator.py::TestSimulator::test_fresh_runs_are_identical
FAILED generator/tests/simulation/test_simulator.py::TestSimulator::test_resume_equals_single_run
2 failed in 22.81s
```

The reference run covers only 10 days at scale 0.1, so behaviour that starts later is not exercised: the same mistake in `CustomerDeletionSimulator`, whose deletions happen 30 or more days after signup, passes both tests. For a behaviour like that, add a test of your own that runs long enough, or test the simulator directly with a snapshot that triggers it.
