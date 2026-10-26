# Extending the source data model

How to change what the generator writes and carry the change through to dbt: which files to touch, in which order, and which tests prove it.

The source tables, volumes and realism rules are defined in the [data platform spec](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) (§4, §5). A change to any of them is a spec change: agree it with the owner and update the spec in the same branch. Paths below are relative to `data-platform/generator/src/sherpa_generator/` unless they start with `data-platform/`.

## How a simulated day flows

`Simulator.run` (`simulation/simulator.py`) simulates one day per transaction:

1. `StoreSnapshotLoader` reads the shop as it stands at the start of the day into a frozen `StoreSnapshot`.
2. `_DaySimulator.simulate` calls the behaviour simulators in `behaviour/` in a fixed order. Each reads the snapshot and queues inserts and updates in a `DayChanges`; none touches the database.
3. `RecordWriter` writes the `DayChanges` with `COPY` and saves the simulation state (last day, primary-key counters) in the same transaction.

Records are frozen Pydantic models in `records/`, one class per raw table; field order is column order. `RawSchema.TABLE_DDL` (`database/raw_schema.py`) holds the matching `CREATE TABLE`, and `TestRawSchema::test_table_columns_match_records` keeps the two in sync. The raw schema is created once, so a new column or table only appears after `make reset`.

## Commands

```bash
uv run --env-file .env pytest data-platform/generator/tests/<path>
make reset && uv run sherpa-generate generate --days 30 --scale 0.1   # small dataset
make build                                                            # dbt, dev target
make test && make lint                                                # lint includes the docs check
```

Finish every change with `make reset && make backfill DAYS=365 && make build`.

## Add a column

Example: `customers.preferred_language` (`de`, `en`, `es`, `fr`, `it`, `nl`).

1. **Record** (`records/shop.py`): a `StrEnum` for a closed set, and the field at the column's position; export new names from `records/__init__.py`.
2. **DDL** (`RawSchema.TABLE_DDL`): the same position. Raw tables are loose: enums are `text`, no foreign keys.
3. **Simulator that sets it**, here `VisitorTrafficSimulator._signup` (`behaviour/visitor_traffic.py`). Prefer deriving the value (from `LOCALE_BY_COUNTRY`) over a new `day.rng` draw: a new draw shifts every later draw that day, so other rows change.
4. **Fixtures and test**: every `Customer(...)` needs the field, so update `make_customer` in `data-platform/generator/tests/helpers.py`; test the rule in `tests/behaviour/test_visitor_traffic.py`.
5. **dbt source YAML** (`data-platform/dbt/models/staging/shop/_shop__sources.yml`): the column with a description.
6. **Staging**: select it in `stg_shop__customers.sql`; document and test it in `_shop__models.yml`. An enum gets `accepted_values` with exactly the `StrEnum` values.
7. **Marts**, if consumers should see it: see [Adding or changing a mart](adding-or-changing-a-mart.md).

## Add a table

Example: `raw_shop.wishlist_items`, the variants customers save.

1. **Record** (`records/shop.py`, exported from `records/__init__.py`). `TABLE` is how the generator finds the table; `KEY` defaults to `("id",)`.

   ```python
   class WishlistItem(RawRecord):
       TABLE: ClassVar[str] = "raw_shop.wishlist_items"

       id: int
       customer_id: int
       variant_id: int
       added_at: AwareDatetime
   ```

2. **DDL**: a `CREATE TABLE IF NOT EXISTS raw_shop.wishlist_items (...)` in `RawSchema.TABLE_DDL`.
3. **Primary key**: `PrimaryKeyAllocator.next_id(WishlistItem)`. Counters are saved per `TABLE` in `raw_meta.simulation_state`; nothing to register.
4. **Snapshot** (`simulation/store_snapshot.py`): a simulator only sees the database through `StoreSnapshot`. Add the field it needs (`wishlist_items: list[WishlistItem]`, to avoid duplicate saves) and load it in `StoreSnapshotLoader.load` with `self._records(WishlistItem)`. Load only what a simulator reads.
5. **Behaviour simulator** (`behaviour/wishlists.py`): module constants for tunables, a class taking its collaborators in the constructor, and `simulate_day(snapshot, changes)`. Its own `STREAM` name gives it a random sequence nobody else consumes, so other tables stay unchanged.

   ```python
   rng = self._random.for_day(snapshot.day, STREAM)
   start, _ = self._calendar.day_bounds(snapshot.day)
   ...
   changes.insert(WishlistItem(id=self._keys.next_id(WishlistItem), ..., added_at=start + timedelta(seconds=rng.randrange(SECONDS_PER_DAY))))
   ```

6. **Wire it into `_DaySimulator`** (`simulation/simulator.py`): construct it, and call it in `simulate`. Order matters: a simulator that needs today's signups or orders must come after the one producing them and take that output as an argument (as `place_orders` takes `purchase_intents`); a simulator updating rows another also updates must come after it. A snapshot-only simulator can sit anywhere.
7. **Tests**:
   - Add the new field to every `StoreSnapshot` construction: `_EMPTY_SNAPSHOT` in `tests/helpers.py` and the `_snapshot` helpers in the behaviour tests.
   - Bump the record-type count in `test_every_record_type_has_a_table_and_unique_columns` (`tests/records/test_records.py`).
   - A unit test file `tests/behaviour/test_wishlists.py` using `make_snapshot` and `make_customer`: the rate, items inside the day, no duplicate save, an empty shop saves nothing.
   - Determinism needs nothing new: `fingerprint` hashes every raw table, so `test_fresh_runs_are_identical` and `test_resume_equals_single_run` (`tests/simulation/test_simulator.py`) cover it.
8. **dbt**: a source entry (every column described), `stg_shop__wishlist_items.sql` following the `with source as (...), renamed as (...) select * from renamed` pattern of the other staging models, and its `_shop__models.yml` entry (`unique` and `not_null` on the key, `relationships` to `stg_shop__customers` and `stg_shop__product_variants`, descriptions). For freshness checks, add `freshness` and `loaded_at_field` to the source, as `orders` has.

```bash
uv run --env-file .env pytest data-platform/generator/tests -q
make reset && uv run sherpa-generate generate --days 30 --scale 0.1 && make build
```

## Add an enum value

Example: payment method `klarna`.

1. Add it to the `StrEnum` (`records/shop.py`).
2. Add its weight to `PAYMENT_METHOD_WEIGHTS` (`behaviour/order_lifecycle.py`); the weights sum to 1. Split payments (R7) do not use them.
3. Add it to the staging `accepted_values` list in `_shop__models.yml`. Until then the build fails with `FAIL 1` (one unexpected value) on regenerated data; use `--scale 0.5`, since a 5 % method may not appear at 0.1.
4. Run `test_order_lifecycle.py` and `test_after_sales.py`. Renaming or removing a value also breaks downstream models filtering on it: search `data-platform/dbt/` for it.

## Add or tune a realism rule

R1-R12 are a fixed list, each paired with the dbt technique that handles it (spec §4.4). A new rule or knob, or a new rate for an existing one, needs the owner's agreement and a spec change first.

- **Rate**: a constant on `RealismRules` (`simulation/realism_rules.py`) with the rule number, e.g. `EMAIL_NOISE_RATE: Final = 0.10  # R3`.
- **Noise**: a `RealismRules` method when the rule changes a value (`noisy_email`, `noisy_country`, `duplicate_events`, `late_delivery_delay`, `is_test_account`, `ad_clicks`), or in the owning simulator when it changes what happens (split and failed payments in `OrderLifecycleSimulator._insert_payments`, soft deletes in `CustomerDeletionSimulator`).
- **Rate test**: `tests/simulation/test_realism_rules.py` draws 20,000 times from a seeded `random.Random` and uses `assert_rate` (±20 % of the spec rate). Change the constant and the test's expected value together, then run it plus the simulators using the rule.
- **Cleanup**: in staging (R3 `lower(trim(email))`, R4 the `country_code_variants` seed, R1 the `row_number()` dedupe in `stg_events__events`, R5 `is_test_account`). A new kind of noise needs new cleanup and a dbt test proving it.

## Change volumes or rates

Tunables are module-level `Final` constants with the spec section in a comment. `--scale` multiplies every daily volume at run time without code changes.

| What | Constant | Module |
|---|---|---|
| Sessions, signups per day | `BASE_DAILY_SESSIONS`, `BASE_DAILY_SIGNUPS` | `behaviour/visitor_traffic.py` |
| Channel mix | `CHANNEL_WEIGHTS` | `behaviour/visitor_traffic.py` |
| Funnel | `PRODUCT_VIEW_RATE`, `ADD_TO_CART_RATE`, `BEGIN_CHECKOUT_RATE`, `PURCHASE_RATE` | `behaviour/visitor_traffic.py` |
| Returning visitors, new-visitor purchases | `RETURNING_VISITOR_SHARE`, `NEW_VISITOR_PURCHASE_RATE` | `behaviour/visitor_traffic.py` |
| Leads, email engagement | `BASE_DAILY_LEADS`, `OPEN_RATE`, `CLICK_RATE` | `behaviour/crm_activity.py` |
| Order lines, discounts, payment methods | `LINE_COUNT_WEIGHTS`, `DISCOUNTED_ORDER_RATE`, `PAYMENT_METHOD_WEIGHTS` | `behaviour/order_lifecycle.py` |
| Order lifecycle | `NEVER_PAID_RATE`, `PAID_CANCELLATION_RATE`, `SHIPPING_DELAY_DAYS`, `DELIVERY_DELAY_DAYS` | `behaviour/order_lifecycle.py` |
| Returns timing | `REQUEST_DELAY_DAYS`, `RECEIPT_DELAY_DAYS` | `behaviour/after_sales.py` |
| Seasonality | `WEEKEND_FACTOR`, `BLACK_FRIDAY_WEEK_FACTOR`, `DECEMBER_FACTOR`, `JANUARY_FACTOR` | `simulation/calendar.py` |
| Customer value spread | `PROPENSITY_SIGMA` | `simulation/store_snapshot.py` |
| Realism-rule rates | class constants of `RealismRules` | `simulation/realism_rules.py` |

Volumes interact (the comments beside `RETURNING_VISITOR_SHARE`, `NEW_VISITOR_PURCHASE_RATE` and `BASE_DAILY_SIGNUPS` explain the tuning), so change one at a time. The behaviour tests check rates on a 30-day run at scale 0.2 (`test_session_conversion_rate`, `test_discount_share`, `test_app_share`). The slow tests simulate 365 days at scale 1.0 (several minutes) into a temporary database:

```bash
uv run --env-file .env pytest -m slow
```

- `test_full_year_volumes`: ~5,000 customers, ~8,000 orders, ~600,000 events, ~6,000 leads (each ±15 %).
- `test_full_year_runtime`: under 5 minutes (spec §5.2); sensitive to load, so rerun on an idle machine before reading a failure as a regression.
- `test_top_buyers_hold_55_to_65_percent_of_net_revenue`: the check for `PROPENSITY_SIGMA` and the visitor purchase rates.

## Keep determinism intact

The same seed, start date, scale and first-run day give identical data, and a resumed run equals one long run.

- All randomness comes from `DeterministicRandom`: `for_day(day, STREAM)` with a stream of your own, `faker_for_day(day, STREAM)` for names and addresses. No global `random`, `uuid.uuid4()` or wall clock in a simulator.
- Never use the built-in `hash()`, and never iterate a set or dict of strings to decide anything (`PYTHONHASHSEED`). Use `stable_int` and sort first.
- Keys come from `PrimaryKeyAllocator`, never Postgres sequences or `max(id) + 1`.
- Decisions about an entity's future (when an order ships, whether a customer is deleted) use `entity_fraction(kind, entity_id, purpose)`, not the day's stream: a resumed run starts with fresh simulators. See `CustomerDeletionSimulator` and the `*_PURPOSE` constants of `OrderLifecycleSimulator`.
- State comes from the snapshot. A simulator-level cache is safe only for values that never change.

```bash
uv run --env-file .env pytest data-platform/generator/tests/simulation/test_simulator.py data-platform/generator/tests/simulation/test_deterministic_random.py -q
```

`test_fresh_runs_are_identical`, `test_resume_equals_single_run` and `test_crash_mid_day_rolls_back_and_resume_matches` run 10 days at scale 0.1, so behaviour that starts later (such as deletions 30+ days after signup) is not exercised: test it directly with a snapshot that triggers it. A Faker method not in `test_faker_output_is_independent_of_pythonhashseed`'s `methods` tuple should be added there.

## Symptoms

| Symptom | Fix |
|---|---|
| `test_table_columns_match_records` fails with `At index N diff` | Record field order and `TABLE_DDL` column order differ; align them. |
| New column or table missing in Postgres | The raw schema is created once: `make reset`, then regenerate. |
| `Missing descriptions:` from `make lint` | Add the column or model to its YAML with a description. |
| `accepted_values` test fails after a new enum value | Add the value to the list in `_shop__models.yml`. |
| `test_every_record_type_has_a_table_and_unique_columns` fails | Update the expected record-type count. |
| `TypeError` or validation error building a `StoreSnapshot` or `Customer` in tests | Add the new field to `helpers.py` and the `_snapshot` helpers. |
| Determinism tests fail after your change | A global `random`, `hash()` or set iteration crept in; use `for_day` and `stable_int`. |
| Rate test fails after changing a constant | Update the test's expected value to the new spec rate. |
