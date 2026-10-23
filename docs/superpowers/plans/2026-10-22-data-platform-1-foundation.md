# data-platform-1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Postgres holding four synthetic e-commerce source systems, filled by a deterministic day-by-day generator built on typed records. On top of it, a dbt project with sources, freshness checks, seeds, documented and tested staging models for all 21 source tables, and `dim_date`.

**Architecture:**
- Docker Compose runs Postgres 16. An init shell script creates the roles.
- The generator (`sherpa_generator`) has four layers:
  - `records/`: one frozen Pydantic model per raw table, plus a StrEnum for every closed value set. This is the source contract.
  - `database/`: the connection, the raw DDL, and a writer that COPYs records.
  - `simulation/`: the day loop and its infrastructure.
  - `behaviour/`: one simulator class per business area.
- A dbt Core project (`dbt/`) declares the raw schemas as sources and builds staging views and `dim_date` on `dev`/`prod`/`ci` targets.

**Tech Stack:** Python 3.12, uv, Pydantic v2 + pydantic-settings, psycopg 3, Faker, Typer, pytest, mutmut; dbt Core ≥ 1.10 + dbt-postgres, dbt_utils; ruff, mypy, sqlfluff (dbt templater), pre-commit; Docker Compose; GNU Make.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md` (§2–§7, §8 data-platform-1, §10). Read §4 (source model), §5 (generator) and §6.1–6.2 (seeds, staging) before starting any task. **Read `CLAUDE.md` (Working Style + Python Guidelines) before writing any code. It is binding.**

## Global Constraints

- **Incremental commits (CLAUDE.md):** each task adds only the files, dependencies, dependency groups, config sections, Makefile targets, `.env` variables and pre-commit hooks that the task itself uses. No placeholders, no stub targets, no empty packages. Each task's "Adds tooling" line lists what it introduces.
- Python 3.12; one root `pyproject.toml` (uv). Dependency groups grow per task: `dev` (pytest, ruff, mypy, pre-commit, mutmut), `generator`, `dbt`.
- ruff: line length 120, rules `E, F, I, UP, B, SIM, ASYNC, T20, LOG, G, C4, TC, PT, RUF`; `[tool.ruff.lint.flake8-type-checking] runtime-evaluated-base-classes = ["pydantic.BaseModel", "pydantic_settings.BaseSettings"]`. mypy on `generator/src` with `disallow_untyped_defs = true`.
- Postgres 16 (`postgres:16-alpine`), database `sherpa`, roles `sherpa_admin` (owner, `CREATEDB`) and `sherpa_agent` (read-only: `default_transaction_read_only = on`, `statement_timeout = 5s`).
- dbt Core: latest stable 1.x, **≥ 1.10**, with `dbt-postgres`. Pin exact versions; pin `dbt-labs/dbt_utils` in `packages.yml`.
- Money: `Decimal`, quantized to cents with `ROUND_HALF_UP`, stored as `numeric(12,2)`, EUR. Shop timestamps are `timestamptz` in UTC.
- **Randomness:** never use Python's built-in `hash()`; derive every seed via `hashlib.sha256`. The generator assigns all primary keys itself (no Postgres sequences). No row ever has a timestamp after the end of "yesterday" (UTC, at run time).
- Records are frozen Pydantic models. **No positional tuples and no bare `dict`s cross a module boundary.** JSON payloads (`context`, `properties`) are typed models.
- No credentials in committed files: `.env` (gitignored) + `.env.example`; `profiles.yml` uses `env_var()` only.
- dbt naming: `stg_<source>__<entity>`; YAML per directory as `_<dir>__models.yml` / `_<source>__sources.yml`. Every staging model and column has a description.
- Schemas: `dev_`-prefixed on `dev`, verbatim on `prod`/`ci`, snapshots always `snapshots`. Prod dbt commands use `--target-path target-prod`.

## Review Focus

1. **Re-running `generate` after it has caught up to yesterday:** it should exit 0 with "nothing to simulate" and no duplicated day. Test: `TestSimulator::test_run_when_caught_up_is_noop` (Task 9).
2. **Duplicate `event_id`s (R1) hitting a unique constraint:** `raw_events.events` must have no PK/unique on `event_id`. Test: `TestRawSchema::test_events_accept_duplicate_event_ids` (Task 2).
3. **Client timestamps with different UTC offsets and DST** (`+01:00` in winter, `+02:00` in summer, `+00:00`): staging must convert them all to the same UTC instant. Test: singular dbt test `assert_events_occurred_at_utc_parsing` (Task 13).
4. **Noisy country codes (R4) the seed doesn't map:** every variant `RealismRules` can emit must exist in `country_codes.raw_value`. Tests: `test_every_noisy_country_variant_is_in_seed` (Task 11) and a staging `relationships` test (Task 12).
5. **Invalid CLI input or state mismatch** (`--days 0`, `--start-date` after yesterday, seed/scale differing from stored state): a clear error, non-zero exit, no writes. Tests in Task 9.

---

## File Structure

```
docker-compose.yml  docker/postgres/init/00_roles.sh  .env.example  Makefile  pyproject.toml
.pre-commit-config.yaml  .sqlfluff  .sqlfluffignore
generator/src/sherpa_generator/
  __init__.py
  settings.py                       # GeneratorSettings
  cli.py                            # Typer app: generate / reset / status
  records/
    __init__.py
    base.py                         # RawRecord, Money, enums shared across systems
    shop.py                         # 16 shop records + shop enums
    events.py                       # TrackedEvent, PendingEvent, EventContext, EventProperties, EventType
    marketing.py                    # Campaign, AdSpendDay
    crm.py                          # Lead, EmailSend + enums
  database/
    __init__.py
    connection.py                   # Database
    raw_schema.py                   # RawSchema
    record_writer.py                # RecordWriter
  simulation/
    __init__.py
    deterministic_random.py         # DeterministicRandom, stable_int
    primary_keys.py                 # PrimaryKeyAllocator
    day_changes.py                  # DayChanges, RecordUpdate
    simulation_state.py             # SimulationState, SimulationStateStore
    realism_rules.py                # RealismRules
    calendar.py                     # SimulationCalendar (seasonality, day boundaries, timezones)
    store_snapshot.py               # StoreSnapshot, StoreSnapshotLoader
    simulator.py                    # Simulator, RunResult, GeneratorError
  behaviour/
    __init__.py
    catalog_setup.py                # CatalogBuilder
    catalog_drift.py                # CatalogDriftSimulator
    visitor_traffic.py              # VisitorTrafficSimulator, PurchaseIntent
    crm_activity.py                 # CrmActivitySimulator
    order_lifecycle.py              # OrderLifecycleSimulator
    after_sales.py                  # AfterSalesSimulator
    ad_spend.py                     # AdSpendSimulator
    customer_deletions.py           # CustomerDeletionSimulator
generator/tests/                    # mirrors the package: records/, database/, simulation/, behaviour/, test_cli.py
  conftest.py                       # temp_database fixture, simulated_days helper
dbt/ …                              # Tasks 11–14
scripts/check_docs_coverage.py      # Task 14
tests/scripts/test_check_docs_coverage.py
```

**Deliberate data-platform-1 scope cuts (from the data platform spec's end state):** `stg_events__events` is a **view** (data-platform-3 converts it to microbatch). `make backfill` calls the generator once (data-platform-3 adds 7-day chunks + `dbt snapshot`, and needs `make reset && make backfill` then). `dbt_expectations` arrives in data-platform-2.

---

### Task 1: Postgres and roles

**Adds tooling:**
- `docker-compose.yml`, `docker/postgres/init/00_roles.sh`, and `.env.example` with the DB variables only.
- `Makefile` with targets `up` and `down` only.
- `pyproject.toml`: project metadata, `requires-python = ">=3.12,<3.13"`, group `dev` = pytest, psycopg[binary], ruff, pre-commit. Ruff config (Global Constraints). Pytest `testpaths = ["generator/tests"]` and marker `integration`.
- `.pre-commit-config.yaml` with ruff check + format and `check-yaml`, `check-toml`, `end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files`.
- `.gitignore` += `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`.

No `generator/src` yet: the role test needs no package.

**Files:** Create the files above plus `generator/tests/database/test_roles.py`.

**Interfaces — Produces:** env vars `POSTGRES_PASSWORD`, `SHERPA_DB_HOST` (`localhost`), `SHERPA_DB_PORT` (`5432`), `SHERPA_DB_NAME` (`sherpa`), `SHERPA_ADMIN_USER` (`sherpa_admin`), `SHERPA_ADMIN_PASSWORD`, `SHERPA_AGENT_USER` (`sherpa_agent`), `SHERPA_AGENT_PASSWORD`.

- [ ] **Step 1:** `docker-compose.yml`: `postgres:16-alpine`, env `POSTGRES_PASSWORD`, `POSTGRES_DB=sherpa`, pass `SHERPA_*` users and passwords through; port `${SHERPA_DB_PORT:-5432}:5432`; named volume `pgdata`; mount `./docker/postgres/init` → `/docker-entrypoint-initdb.d:ro`; healthcheck `pg_isready`.
- [ ] **Step 2:** `00_roles.sh` (`set -euo pipefail`, `psql -v ON_ERROR_STOP=1` passing users/passwords as psql variables):
  - create `sherpa_admin` with LOGIN, CREATEDB and a password; `ALTER DATABASE sherpa OWNER TO sherpa_admin`
  - create `sherpa_agent` with LOGIN and a password; set `default_transaction_read_only = on` and `statement_timeout = '5s'` on the role
  - `REVOKE ALL ON SCHEMA public FROM PUBLIC`; `REVOKE CREATE ON DATABASE sherpa FROM PUBLIC`

  Commit the file as executable (`git update-index --chmod=+x`).
- [ ] **Step 3: Write failing tests** `test_roles.py` (marker `integration`; connect with psycopg using the env vars):
  - `test_agent_sessions_are_read_only_with_timeout`: as `sherpa_agent`, `SHOW default_transaction_read_only` == `'on'` and `SHOW statement_timeout` == `'5s'`.
  - `test_agent_cannot_create_objects`: `CREATE TABLE public.x(i int)` raises `ReadOnlySqlTransaction` or `InsufficientPrivilege`, and the transaction rolls back.
  - `test_agent_cannot_escape_read_only_by_default_in_new_session`: after the agent runs `SET default_transaction_read_only = off` in one connection, a **new** connection is still read-only. This pins down that the role default holds.
  - `test_admin_owns_database_and_can_create_databases`: `pg_get_userbyid(datdba)` == `'sherpa_admin'` and `rolcreatedb` is true.
- [ ] **Step 4:** `make up && uv run pytest -m integration -v`. Expected: PASS. If the volume predates the script: `make down && docker volume rm <project>_pgdata && make up`.
- [ ] **Step 5:** `uv run pre-commit run --all-files` passes. Commit `feat: postgres with sherpa_admin and read-only sherpa_agent roles`.

---

### Task 2: Raw source records and schema

**Adds tooling:**
- Package `generator/src/sherpa_generator` (hatchling, `packages = ["generator/src/sherpa_generator"]`).
- Group `generator` = pydantic, pydantic-settings.
- mypy in `dev` + `[tool.mypy]` (`files = ["generator/src"]`, `disallow_untyped_defs = true`) + a local pre-commit mypy hook; `.gitignore` += `.mypy_cache/`.
- Pytest marker `integration` is reused.

**Files:**
- Create `settings.py`, `records/{__init__,base,shop,events,marketing,crm}.py`, `database/{__init__,connection,raw_schema}.py`.
- Tests: `generator/tests/conftest.py`, `generator/tests/records/test_records.py`, `generator/tests/database/test_raw_schema.py`.

**Interfaces — Produces:**
- `GeneratorSettings(BaseSettings)` (frozen, env prefix `SHERPA_`, reads `.env`): `db_host: str`, `db_port: int`, `db_name: str`, `admin_user: str`, `admin_password: SecretStr`; `conninfo(dbname: str | None = None) -> str`.
- `Database(settings: GeneratorSettings, dbname: str | None = None)`:
  - `connect() -> psycopg.Connection` (autocommit off)
  - `transaction() -> ContextManager[psycopg.Connection]` (commits on success, rolls back on exception)
- `records.base`:
  - `Money = Annotated[Decimal, AfterValidator(quantize to 0.01 ROUND_HALF_UP)]`
  - `RawRecord(BaseModel)`: frozen, `extra="forbid"`; `TABLE: ClassVar[str]` (schema-qualified); `KEY: ClassVar[tuple[str, ...]] = ("id",)`
  - `RawRecord.columns() -> tuple[str, ...]` (classmethod, field names in declaration order)
  - `RawRecord.copy_values() -> tuple[object, ...]`: field values in column order. Nested models are dumped `mode="json"` (for `jsonb`), enums become `.value`.
- One record class per table in the data platform spec §4, **field names and order exactly as the spec's column lists**:
  - **Shop:** `Customer`, `Address`, `Category`, `Product`, `ProductVariant`, `Order`, `OrderItem`, `OrderStatusChange`, `Payment`, `Shipment`, `Promotion`, `CouponCode`, `OrderDiscount`, `Return`, `ReturnItem`, `Refund`
  - **Events:** `TrackedEvent`, plus `PendingEvent(TrackedEvent)` with `TABLE = "raw_meta.pending_events"`
  - **Marketing:** `Campaign`, `AdSpendDay` (`KEY = ("spend_date", "campaign_id")`)
  - **CRM:** `Lead`, `EmailSend`
- StrEnums (values = the spec's lowercase literals) for every closed set: `AddressType`, `OrderStatus`, `SalesChannel`, `PaymentMethod`, `PaymentStatus`, `PromotionType`, `ReturnReason`, `ReturnStatus`, `RefundReason`, `EventType`, `DeviceType`, `CampaignChannel`, `LeadSource`, `LifecycleStage`.
- `TrackedEvent`:
  - `occurred_at: str`: ISO-8601 with offset, validated by a regex.
  - `context: EventContext` (`device_type`, `utm_source`, `utm_medium`, `utm_campaign`, `referrer`, all optional except `device_type`).
  - `properties: EventProperties` (`page_path`, `product_id`, `variant_id`, `order_id`, `cart_value: Money | None`, all optional).
- `RawSchema(db: Database)`:
  - `create() -> None`: idempotent; creates schemas `raw_shop, raw_events, raw_marketing, raw_crm, raw_meta` and all tables.
  - `drop() -> None`.
  - `tables() -> dict[str, tuple[str, ...]]`: table → columns, read back from `information_schema`.
  - DDL rules:
    - `bigint PRIMARY KEY` on `id`, and the composite PK on `ad_spend_daily`
    - **no PK/unique on `raw_events.events` or `raw_meta.pending_events`**
    - enum columns are `text`
    - FK columns are plain `bigint` (no constraints)
    - `jsonb` for `context`/`properties`; `numeric(12,2)` for money
    - `raw_meta.simulation_state` has columns as in the data platform spec §5.3: `id int PK CHECK (id = 1)`, `seed bigint`, `scale numeric`, `start_date date`, `last_simulated_date date`, `next_ids jsonb`, `updated_at timestamptz`
- Fixture `temp_database` (conftest): creates `sherpa_test_<uuid8>` as admin, yields a `Database` with `RawSchema.create()` applied, then drops it. Marked `integration`.

- [ ] **Step 1: Write failing tests:**
  - `test_records.py`:
    - `test_every_record_type_has_a_table_and_unique_columns` (iterates all `RawRecord` subclasses).
    - `test_money_quantizes_half_up`: `Decimal("1.005")` → `Decimal("1.01")`.
    - `test_record_rejects_unknown_fields_and_invalid_enum_values`: `Order(..., status="shipping")` raises; `extra` raises.
    - `test_tracked_event_rejects_occurred_at_without_offset`: `"2025-07-01T14:03:22"` raises.
    - `test_copy_values_serializes_enums_and_nested_payloads`: an `Order`'s status becomes `"paid"`; a `TrackedEvent`'s context becomes a `dict` with only set keys.
  - `test_raw_schema.py`, `class TestRawSchema` (integration):
    - `test_create_is_idempotent`
    - `test_table_columns_match_records`: for every record type, `tables()[T.TABLE] == T.columns()`. This keeps the DDL and the models in sync.
    - `test_events_accept_duplicate_event_ids`
    - `test_drop_removes_all_raw_schemas`
- [ ] **Step 2:** Run `uv run pytest generator/tests -v`. Expected: FAIL (imports missing).
- [ ] **Step 3:** Implement `settings.py`, `records/*`, `database/connection.py`, `database/raw_schema.py`. DDL lives as one SQL string per table in `raw_schema.py`, ordered to match the records.
- [ ] **Step 4:** Re-run tests and `uv run mypy`. Expected: PASS, no mypy errors.
- [ ] **Step 5:** Commit `feat: typed raw source records and raw schema`.

---

### Task 3: Simulation infrastructure — randomness, keys, changes, writer, state

**Adds tooling:** group `generator` += faker, tzdata.

**Files:**
- Create `simulation/{__init__,deterministic_random,primary_keys,day_changes,simulation_state}.py`, `database/record_writer.py`.
- Tests: `generator/tests/simulation/{test_deterministic_random,test_primary_keys,test_day_changes,test_simulation_state}.py`, `generator/tests/database/test_record_writer.py`.

**Interfaces — Produces:**
- `stable_int(*parts: object) -> int`: module-level utility; the first 8 bytes of `sha256(":".join(map(str, parts)))`.
- `DeterministicRandom(seed: int)`:
  - `for_day(day: date) -> random.Random`
  - `faker_for_day(day: date) -> Faker`: locales `de_DE, fr_FR, es_ES, it_IT, nl_NL, en_GB, de_AT, nl_BE`
  - `entity_fraction(kind: str, entity_id: int, purpose: str) -> float` in `[0, 1)`: for decisions about an entity's future, re-derived identically on resume.
- `PrimaryKeyAllocator(next_ids: Mapping[str, int])`:
  - `next_id(record_type: type[RawRecord]) -> int`: keyed by `record_type.TABLE`; starts at 1.
  - `next_ids() -> dict[str, int]`
- `RecordUpdate[T: RawRecord]` (frozen Pydantic, generic): `record_type: type[T]`, `key: dict[str, object]`, `values: dict[str, object]`.
- `DayChanges`:
  - `insert(record: RawRecord) -> None`
  - `update(record_type: type[RawRecord], key: int | tuple, **values: object) -> None`: rejects field names not in `record_type.columns()` and values that fail that field's validation (`TypeAdapter` of the field annotation), raising `ValueError`.
  - `remove_pending(event_ids: Iterable[str]) -> None`
  - `inserted(record_type) -> list[T]`: for tests and cross-simulator reads within a day.
  - `insert_counts() -> dict[str, int]`
- `RecordWriter(conn: psycopg.Connection)`:
  - `write(changes: DayChanges) -> dict[str, int]`: one `COPY <table> (<columns>) FROM STDIN` per record type using `copy_values()`, `Jsonb` for nested payloads; then updates via `executemany`; then pending deletions. Never commits.
- `SimulationState` (frozen Pydantic): `seed: int`, `scale: float`, `start_date: date`, `last_simulated_date: date | None`, `next_ids: dict[str, int]`.
- `SimulationStateStore(conn)`: `load() -> SimulationState | None`; `save(state: SimulationState) -> None` (upsert id = 1, `updated_at = now()`).

- [ ] **Step 1: Write failing tests:**
  - `TestDeterministicRandom`:
    - `test_for_day_is_stable_across_processes`: subprocesses with `PYTHONHASHSEED=1` and `=2` print `for_day(date(2025,1,1)).random()` for seed 42; both equal the in-process value.
    - `test_entity_fraction_depends_on_every_part`: changing kind, id or purpose changes the value; values are in `[0,1)`.
    - `test_faker_for_day_is_reproducible`: two instances yield the same first 5 names.
  - `TestPrimaryKeyAllocator::test_next_id_continues_from_saved_counters`: `{Order.TABLE: 10}` → 10, then 11; `Customer` → 1; `next_ids()` reflects both.
  - `TestDayChanges`:
    - `test_update_rejects_unknown_column`
    - `test_update_rejects_invalid_value` (`status="shipping"` on `Order`)
    - `test_inserted_returns_records_of_one_type_in_insertion_order`
  - `TestRecordWriter` (integration):
    - `test_write_round_trips_records_including_jsonb_and_enums`: write 1 `Customer`, 1 `TrackedEvent`; read back typed values.
    - `test_write_applies_updates_after_inserts_in_same_batch`: insert an `Order` and update its status in the same `DayChanges`.
    - `test_write_does_not_commit`: rollback leaves no rows.
  - `TestSimulationStateStore` (integration):
    - `test_load_returns_none_when_empty`
    - `test_save_then_load_round_trips`
    - `test_save_overwrites_single_row`
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Re-run + mypy. Expected: PASS.
- [ ] **Step 5:** Commit `feat: deterministic randomness, key allocation, typed day changes, COPY writer, state store`.

---

### Task 4: Realism rules and calendar

**Adds tooling:**
- `mutmut` in `dev` + `[tool.mutmut]` (`paths_to_mutate` empty; passed per run).
- `Makefile` target `mutate` (`uv run mutmut run --paths-to-mutate $(MODULE) && uv run mutmut results`).
- `.gitignore` += mutmut's working files (`mutants/`, `.mutmut-cache`).

**Files:**
- Create `simulation/realism_rules.py`, `simulation/calendar.py`.
- Tests: `generator/tests/simulation/test_realism_rules.py`, `test_calendar.py`.

**Interfaces — Produces:**
- `RealismRules` (constructor takes no args; rates are class-level `Final` constants named after the spec rule, e.g. `DUPLICATE_EVENT_RATE = 0.01  # R1`).
  - Constants: R1 0.01, R2 0.02, R3 0.10, R4 0.03, R5 0.005 + `TEST_EMAIL_DOMAIN = "sqlsherpa-test.com"`, R6 0.01, R7 failed 0.03 / split 0.05, R8 0.03, R10 list 0.003 / cost 0.001, R12 `APP_ORDER_SHARE = 0.35`.
  - `COUNTRY_WEIGHTS`: DE .30, FR .20, ES .15, IT .12, NL .08, GB .08, AT .04, BE .03.
  - `noisy_country_variants() -> dict[str, tuple[str, ...]]`: each code → its lowercase form; `GB` additionally `UK`, `uk`.
  - Methods (each takes `r: random.Random`):
    - `duplicate_events(events: Sequence[TrackedEvent], r) -> list[TrackedEvent]` (R1: copies share `event_id` and `received_at` UTC day; `received_at` + 0–300 s, clamped to that day)
    - `late_delivery_delay(r) -> timedelta` (R2)
    - `noisy_email(email, r) -> str` (R3)
    - `noisy_country(code, r) -> str` (R4)
    - `is_test_account(r) -> bool` (R5)
    - `ad_clicks(paid_sessions: int, r) -> int` (R11)
- `SimulationCalendar`:
  - `seasonality(day: date) -> float`: weekend ×1.2; Black Friday week (Mon–Sun containing the 4th Friday of November) ×3; December ×1.6; January ×0.7; factors multiply.
  - `day_bounds(day) -> tuple[datetime, datetime]` (UTC `[start, end)`)
  - `black_friday_week(year) -> tuple[date, date]`
  - `TIMEZONES_BY_COUNTRY`: DE `Europe/Berlin`, FR `Europe/Paris`, ES `Europe/Madrid`, IT `Europe/Rome`, NL `Europe/Amsterdam`, GB `Europe/London`, AT `Europe/Vienna`, BE `Europe/Brussels`
  - `local_iso(instant: datetime, country: str) -> str` (e.g. `2025-07-01T14:03:22+02:00`)

- [ ] **Step 1: Write failing tests** (20,000 draws with `random.Random(1)`; tolerance ±20% relative):
  - **Rates:** `test_duplicate_rate`, `test_late_delay_rate_and_range` (non-zero delays all in `[1 d, 4 d)`), `test_email_noise_rate`, `test_country_noise_rate`, `test_test_account_rate`.
  - **Invariants:**
    - `test_duplicates_share_event_id_and_received_day` (incl. an original at 23:59:30 UTC: the duplicate stays on that day)
    - `test_noisy_email_normalizes_back` (`strip().lower()` == original)
    - `test_ad_clicks_bounds_and_zero_sessions` (0 sessions → 0 clicks)
    - `test_duplicate_events_of_empty_list_is_empty`
  - **Calendar:**
    - `test_seasonality_examples`: 2025-11-26 → 3.0; 2025-11-29 (Sat) → 3.6; 2025-01-15 → 0.7; 2025-12-06 (Sat) → 1.92
    - `test_black_friday_week_2025_and_2026`: (2025-11-24, 2025-11-30) and (2026-11-23, 2026-11-29)
    - `test_local_iso_respects_dst`: the same UTC instant in January vs July gives `+01:00` vs `+02:00` for DE and `+00:00` vs `+01:00` for GB
- [ ] **Step 2:** Run. Expected: FAIL. Implement. Re-run. Expected: PASS.
- [ ] **Step 3: Mutation run:** `make mutate MODULE=generator/src/sherpa_generator/simulation/calendar.py`. Kill surviving mutants with tests, or justify them in a comment in the test file. Expected: no unjustified survivors.
- [ ] **Step 4:** Commit `feat: realism rules R1–R12 and simulation calendar`.

---

### Task 5: Catalog and marketing setup

**Files:** Create `behaviour/{__init__,catalog_setup}.py`. Test: `generator/tests/behaviour/test_catalog_setup.py`.

**Interfaces — Produces:** `CatalogBuilder(random: DeterministicRandom, keys: PrimaryKeyAllocator)` with `build(changes: DayChanges, start_date: date, end_date: date) -> None`. It inserts `Category`, `Product`, `ProductVariant`, `Campaign`, `Promotion`, `CouponCode`. It is not scaled: scale applies to daily volumes only.

Decisions (unchanged from the data platform spec §5.2 "Setup"; values pinned here):
- **Categories:**
  - L1 exactly `Footwear, Apparel, Accessories, Equipment`.
  - 12 L2 (3 per L1); 25 L3 spread so that some L2 are leaves (depth 2).
  - Products attach to leaves only.
- **Products and variants:**
  - 300 products across 25 brands, named `"<Brand> <Model> <Leaf>"`.
  - Sizes: footwear `38–46`, apparel `XS–XXL`, others `one-size`; 1–3 colours → ~1,500 variants (±15%), unique SKUs.
  - `list_price` by L1: Footwear 60–180, Apparel 20–120, Accessories 10–80, Equipment 30–300, ending in `.99`/`.95`.
  - `cost_price = list_price × U(0.30, 0.60)`.
- **Campaigns** (~15):
  - Always-on for `paid_search`, `paid_social`, `affiliate`, spanning the whole range.
  - Seasonal campaigns on `paid_search`/`paid_social`; ≥ 4 `email` campaigns.
  - `utm_campaign` = slug of the name; budget 2k–40k. No `display` campaigns.
- **Promotions** (~20):
  - `Black Friday 20%` (`percent`, 20) for the Black Friday week from `SimulationCalendar`.
  - Seasonal `percent` 10–30; `fixed` 5/10/15 with `min_order_value` 50/75/100; two `free_shipping`.
  - Promotions tied to email/seasonal campaigns carry `campaign_id`.
  - 1–3 coupon codes (8 uppercase chars) per email-campaign promotion.

- [ ] **Step 1: Write failing tests** (`class TestCatalogBuilder`, seed 42, range 2025-01-01..2025-12-31, assertions read records via `changes.inserted(...)`):
  - `test_category_tree_has_four_roots_max_depth_three_and_products_on_leaves`
  - `test_catalog_sizes_and_unique_skus`
  - `test_cost_ratio_bounds`
  - `test_black_friday_promotion_matches_calendar_week`
  - `test_always_on_campaigns_cover_range`
  - `test_build_is_deterministic`: identical records across two builds
  - `test_short_range_without_november_has_no_black_friday_promotion` (2025-01-01..2025-03-31)
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → mypy clean.
- [ ] **Step 3:** Commit `feat: catalog, campaign and promotion setup`.

---

### Task 6: Store snapshot, visitor traffic and CRM activity

**Files:**
- Create `simulation/store_snapshot.py`, `behaviour/visitor_traffic.py`, `behaviour/crm_activity.py`.
- Tests: `generator/tests/simulation/test_store_snapshot.py`, `generator/tests/behaviour/{test_visitor_traffic,test_crm_activity}.py`.
- Modify `generator/tests/conftest.py`: add the helper `simulated_days(db, days, scale, start, through: Literal["traffic","orders","all"])`, which runs setup plus the requested simulators day by day (one transaction per day). Tasks 7–8 extend `through`.

**Interfaces — Produces:**
- `StoreSnapshot` (frozen Pydantic), reusing record models instead of new types:
  - `day: date`
  - `variants: list[ProductVariant]`, `product_category_l1: dict[int, str]`, `variant_popularity: dict[int, float]` (Zipf by product rank, split over variants)
  - `customers: list[Customer]`, `customer_devices: dict[int, list[str]]`, `leads: list[Lead]`
  - `active_promotions: list[Promotion]`, `coupon_codes: list[CouponCode]`, `active_campaigns: dict[CampaignChannel, list[Campaign]]`
  - `open_orders: list[Order]`, `recently_delivered_orders: list[Order]`, `open_returns: list[Return]` (used by Tasks 7–8)
  - `propensity(customer_id) -> float`: lognormal(0, 1.1) via the inverse CDF of `entity_fraction("customer", id, "propensity")`
  - All lists are ordered by key.
- `StoreSnapshotLoader(conn, random: DeterministicRandom)`: `load(day: date) -> StoreSnapshot`.
- `PurchaseIntent` (frozen Pydantic): `customer_id: int`, `sales_channel: SalesChannel`, `purchased_at: datetime`, `session_anonymous_id: str | None`, `utm_campaign: str | None`, `purchase_event_id: str | None`.
- `VisitorTrafficSimulator(random, keys, rules: RealismRules, calendar: SimulationCalendar, scale: float)`:
  - `simulate_day(snapshot, changes) -> TrafficOutcome`
  - `TrafficOutcome` (frozen): `purchase_intents: list[PurchaseIntent]`, `paid_sessions_by_campaign: dict[int, int]`, `new_customers: list[Customer]`
  - Writes `TrackedEvent`/`PendingEvent`, new `Customer` + `Address`. Web purchase events carry no `order_id`: Task 7 inserts the order and the event refers to it through `purchase_event_id` (see Task 7).
- `CrmActivitySimulator(random, keys, rules, scale)`: `simulate_day(snapshot, changes, new_customers: list[Customer]) -> None`. Writes `Lead`, `EmailSend` and lead stage updates (`subscriber → lead` on first open, `→ mql` on first click, `→ customer` 1–2 days after a matching signup).

Decisions: as the data platform spec §5.2 steps 2–4.
- **Sessions:** Poisson(400 × scale × seasonality).
- **Channel mix:** direct .25, organic .25, paid_search .18, paid_social .12, email .10, affiliate .05, referral .05.
- **UTMs:** paid_search `google/cpc`; paid_social `facebook|instagram/paid_social`; email `newsletter/email`; affiliate `awin/affiliate`; referral and organic set `referrer` only; direct has nothing.
- **Visitors:** 30% of sessions come from existing customers (∝ propensity; a new device with p .1, up to 3), and 60% of those log in first.
- **Funnel:** product views p .70 (1–6), add_to_cart .30, begin_checkout .45, purchase .70.
- **Checkout:** at checkout, unidentified sessions emit `login` or `signup`. Inter-event gaps are 10–240 s.
- **Session start times:** hour weights peak 12–14 and 19–22 UTC.
- **Event timestamps:** `occurred_at` via `calendar.local_iso`. `received_at = occurred + U(0.2, 5) s + late_delivery_delay`. If `received_at` is beyond the day, the event becomes a `PendingEvent`. R1 applies to events landing today.
- **Signups:** total ≈ Poisson(14 × scale × seasonality). 35% reuse an open lead's email; then R3; R5; opt-in .55; 1–3 addresses with R4.
- **Leads:** Poisson(16 × scale); sources newsletter .45 / popup .30 / giveaway .15 / referral .10.
- **Email:** newsletter every Tuesday to every lead; each active email campaign sends on its start weekday to 30% of leads; opens .25 within 48 h; clicks .03, only if opened.
- **App purchases:** `round(web × 0.35 / 0.65)`, ∝ propensity, with no events.

- [ ] **Step 1: Write failing tests** (integration; `simulated_days(..., days=30, scale=0.2, start=2025-03-01, through="traffic")`):
  - `TestStoreSnapshotLoader`:
    - `test_lists_are_ordered_by_key_and_exclude_deleted_customers_from_visitors`
    - `test_propensity_is_stable_per_customer`
  - `TestVisitorTrafficSimulator`:
    - `test_session_conversion_rate` (web purchases / sessions in `[0.025, 0.045]`)
    - `test_paid_search_share` (`utm_medium = 'cpc'` share in `[0.14, 0.22]`)
    - `test_no_received_at_beyond_last_simulated_day_and_pending_events_are_future`
    - `test_new_visitor_events_are_anonymous_until_login_or_signup`
    - `test_zero_scale_day_produces_no_sessions`: unit; `scale=0.0`, so empty outcome and no records
    - `test_day_without_active_paid_campaigns_reassigns_paid_share`: unit with a snapshot that has no active campaigns, so no paid UTMs and no crash
  - `TestCrmActivitySimulator`:
    - `test_lead_match_rate_of_customers` (`[0.25, 0.45]`)
    - `test_lifecycle_never_moves_backwards`
    - `test_newsletter_only_on_tuesdays`
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → mypy clean.
- [ ] **Step 3:** Commit `feat: store snapshot, visitor traffic and CRM activity`.

---

### Task 7: Order lifecycle

**Files:** Create `behaviour/order_lifecycle.py`. Test: `generator/tests/behaviour/test_order_lifecycle.py`. Modify the conftest helper (`through="orders"`).

**Interfaces — Produces:**
- `OrderLifecycleSimulator(random, keys, rules, calendar)`:
  - `place_orders(snapshot, changes, intents: list[PurchaseIntent]) -> list[Order]`: creates `Order`, `OrderItem`, `OrderDiscount`, the first `OrderStatusChange`, and `Payment`s. For web intents, it **replaces** the pending/landing purchase event with a copy carrying `properties.order_id` (records are frozen; `DayChanges` exposes `replace(old, new)`, added in this task with a test).
  - `progress_orders(snapshot, changes) -> None`: status transitions due today, `Shipment`s, cancellation `Refund`s.
- `OrderPricing` (pure, public, used by Task 8 too):
  - `subtotal(items) -> Money`
  - `discount_amount(promotion, subtotal) -> Money`
  - `shipping_amount(subtotal, discount_total, free_shipping: bool) -> Money`
  - `tax_amount(subtotal, discount_total) -> Money`
  - `allocate_discount(line_gross: Sequence[Money], discount: Money) -> list[Money]`: pro rata; the remainder goes to the largest line, so the lines sum exactly.
  - Constants `SHIPPING_FEE = Decimal("4.95")`, `FREE_SHIPPING_THRESHOLD = Decimal("75")`, `TAX_RATE = Decimal("0.20")`.

Decisions (the data platform spec §5.2 steps 5–6):
- **Lines and discounts:**
  - Lines 1–4 (weights .5/.3/.15/.05); quantity 1 (85%), else 2–3.
  - 25% of orders get one eligible promotion; R8 adds a coupon.
  - `free_shipping` → a discount row with amount 0.00 and shipping waived.
- **Totals:** `order_number = f"SS-{id:06d}"`; `order_total = subtotal − discount_total + shipping + tax`.
- **Payments:**
  - Method: card .70 / paypal .25 / gift_card .05. R7: split payments, and a failed attempt first.
  - 2% are never paid: they keep only an `authorized` payment, stay `placed`, and are cancelled the next day.
  - Otherwise `paid` 1–30 min after `placed`.
- **Lifecycle,** via `entity_fraction(..., "order", id, purpose)`:
  - 3% of paid orders are cancelled 1 day after being paid, with a cancellation refund of the captured amount on the largest captured payment.
  - Otherwise `shipped` after 1–3 days (DHL/UPS/DPD/GLS, cost 3.50–8.00), then `delivered` after 2–5 more days.
  - Each transition adds a status-change record plus an `Order` update.

- [ ] **Step 1: Write failing tests:**
  - `TestOrderPricing` (unit):
    - `test_order_total_formula_examples`
    - `test_shipping_free_at_exactly_75_after_discount`: 74.99 → 4.95; 75.00 → 0
    - `test_tax_rounds_half_up`
    - `test_allocate_discount_sums_exactly_and_remainder_goes_to_largest_line`: lines 33.33/33.33/33.34, discount 10.00
    - `test_allocate_zero_discount_and_single_line`
    - `test_fixed_discount_never_exceeds_subtotal`
  - `TestOrderLifecycleSimulator` (integration, 30 days, `through="orders"`):
    - `test_order_totals_are_consistent_with_items_and_discounts`
    - `test_captured_payments_equal_total_for_paid_orders`
    - `test_status_history_is_monotonic_and_matches_current_status`
    - `test_discount_share` (`[0.18, 0.32]`)
    - `test_app_share` (`[0.28, 0.42]`)
    - `test_every_web_order_has_exactly_one_purchase_event` (events + pending)
    - `test_never_paid_orders_are_cancelled_next_day_without_refund`
  - `TestDayChanges::test_replace_swaps_record_in_place` (in `test_day_changes.py`)
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → mypy clean.
- [ ] **Step 3:** `make mutate MODULE=generator/src/sherpa_generator/behaviour/order_lifecycle.py` scoped to `OrderPricing` (use `--paths-to-mutate` on the file; ignore survivors outside `OrderPricing` in the report). No unjustified survivors in `OrderPricing`.
- [ ] **Step 4:** Commit `feat: order lifecycle with pricing, payments and status progression`.

---

### Task 8: After-sales, ad spend, catalog drift and customer deletions

**Files:**
- Create `behaviour/{after_sales,ad_spend,catalog_drift,customer_deletions}.py`.
- Tests: one test module each under `generator/tests/behaviour/`. Modify the conftest helper (`through="all"`).

**Interfaces — Produces** (each a class with `simulate_day(snapshot, changes, …) -> None`):
- `AfterSalesSimulator(random, keys, calendar)`:
  - return requests, approvals and rejections, receipts, return refunds, goodwill refunds
  - public pure helpers `return_refund_amount(items: Sequence[OrderItem], allocated: Sequence[Money], returned_qty: Mapping[int, int]) -> Money` and `goodwill_amount(order: Order, fraction: float) -> Money`
- `AdSpendSimulator(random, rules)`: `simulate_day(snapshot, changes, paid_sessions_by_campaign: Mapping[int, int])`.
- `CatalogDriftSimulator(random, rules)`: R10 price changes; 0.1% of products toggle `is_active`; `updated_at` bumped.
- `CustomerDeletionSimulator(random)`: R6. A customer is deleted when `entity_fraction(..,"delete") < 0.01`, on `created_at + 30 + ⌊entity_fraction(..,"delete_after") × 170⌋` days.

Decisions (the data platform spec §5.2 steps 1, 7, 8):
- **Returns:**
  - Decided at delivery: p .12, doubled if any line is Footwear (cap .24).
  - Reason `size` for footwear; otherwise damaged .2 / not_as_described .3 / changed_mind .5.
  - Requested 1–30 days after delivery, covering 1..all lines with quantity 1..qty.
  - Approved after 1–2 days (10% rejected instead); received 3–10 days after the request (restockable .8).
  - Refund on receipt = Σ returned qty × (unit price − allocated share) × 1.20.
- **Goodwill:** 1% of delivered orders, 1–20 days after delivery, amounting to `U(.05,.20) × (subtotal − discount + tax)`.
- **Ad spend:**
  - `spend = budget / campaign_days × U(0.7, 1.3)`
  - `impressions = round(spend × U(80, 150))`
  - clicks via R11

- [ ] **Step 1: Write failing tests:**
  - `TestAfterSalesSimulator`:
    - `test_return_refund_uses_allocated_discount`: lines 60/40, discount 10, return 1 × the 60 line → 64.80
    - `test_goodwill_never_refunds_shipping`
    - `test_refunds_never_exceed_captured_per_order` (integration)
    - `test_rejected_returns_get_no_refund` (integration)
    - `test_return_requested_only_after_delivery` (integration)
  - `TestAdSpendSimulator`:
    - `test_spend_only_for_active_paid_campaigns`
    - `test_zero_paid_sessions_gives_zero_clicks_but_spend`
  - `TestCatalogDriftSimulator::test_price_change_rate_and_updated_at_bumped`
  - `TestCustomerDeletionSimulator`:
    - `test_deletion_rate_and_timing_window`
    - `test_deleted_customer_is_never_deleted_twice`
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → mypy clean.
- [ ] **Step 3:** `make mutate` on `after_sales.py` (pure helpers). No unjustified survivors there.
- [ ] **Step 4:** Commit `feat: after-sales, ad spend, catalog drift and customer deletions`.

---

### Task 9: Simulator and CLI

**Adds tooling:**
- Group `generator` += typer; console script `sherpa-generate = "sherpa_generator.cli:app"`.
- `Makefile` targets `generate` (`DAYS` required), `backfill` (`DAYS ?= 365`), `status`, `reset` (generator only; dbt schemas are added in Task 14).

**Files:** Create `simulation/simulator.py`, `cli.py`. Tests: `generator/tests/simulation/test_simulator.py`, `generator/tests/test_cli.py`.

**Interfaces — Produces:**
- `GeneratorError(Exception)`.
- `RunResult` (frozen): `first_day: date | None`, `last_day: date | None`, `rows_written: dict[str, int]`.
- `Simulator(db: Database, *, seed: int, scale: float, today: date)`:
  - `run(days: int, start_date: date | None = None) -> RunResult`
  - `today` is injected; the CLI passes the current UTC date.
  - The constructor wires every collaborator (`DeterministicRandom`, `RealismRules`, `SimulationCalendar`, the behaviour simulators). There is no global state.
- CLI: `generate --days N [--start-date] [--scale 1.0] [--seed 42]`, `reset`, `status`. Exit codes: 0 for success or no-op, 2 for `GeneratorError` (message on stderr).

Behaviour (the data platform spec §5.1, §5.3):
- **Errors:** `days < 1`; `start_date > yesterday`; seed/scale differing from the stored state; `start_date` given while state exists.
- **First run:** setup + initial state in one transaction; `start_date` defaults to `yesterday − days + 1`.
- **Range:** from `last_simulated_date + 1` for `days`, clipped at yesterday. Nothing to do → `RunResult(None, None, {})`.
- **Per day, in one transaction** (`Database.transaction()`):
  1. load the snapshot
  2. flush `PendingEvent`s whose `received_at` falls on that day (insert as `TrackedEvent`, remove pending)
  3. catalog drift
  4. visitor traffic
  5. CRM activity
  6. place orders
  7. progress orders
  8. after-sales
  9. ad spend
  10. customer deletions
  11. write the changes and save the state (`last_simulated_date`, `next_ids`)

- [ ] **Step 1: Write failing tests** (integration; `today = date(2025,4,1)` unless stated). Each test is a method on `TestSimulator`, `TestCli`.
  - **Fingerprint:** per table `count(*)` + `md5(string_agg(<key>::text, ',' ORDER BY <key>))`; for events, `event_id || received_at`.
  - **Determinism:**
    - `test_fresh_runs_are_identical`: 10 days, seed 7, scale 0.1, from 2025-03-01, on two databases.
    - `test_resume_equals_single_run`: 4 + 6 days vs 10 days, including pending events.
    - `test_crash_mid_day_rolls_back_and_resume_matches`: monkeypatch `CustomerDeletionSimulator.simulate_day` to raise on day 3. Then `last_simulated_date == start + 1`, nothing from day 3 is written, and a later normal run matches the uninterrupted fingerprint.
  - **Range handling:**
    - `test_run_when_caught_up_is_noop`
    - `test_never_beyond_yesterday` (`days=400` from 2025-03-25 → last day 2025-03-31)
  - **Rejected input:**
    - `test_start_date_after_yesterday_rejected`
    - `test_seed_mismatch_rejected`
    - `test_start_date_with_existing_state_rejected`

    Each of these raises `GeneratorError` and leaves no rows.
  - **CLI:**
    - `TestCli::test_rejects_zero_days`: exit 2, stderr mentions `--days`
    - `TestCli::test_status_reports_range_and_order_count`
    - `TestCli::test_reset_drops_and_recreates_raw_schemas`
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → mypy clean.
- [ ] **Step 3:** Commit `feat: day-by-day simulator and generator CLI`.

---

### Task 10: Volume and runtime check

**Adds tooling:** pytest marker `slow`; `addopts = '-m "not slow"'`.

**Files:** Create `generator/tests/simulation/test_full_year_volume.py`.

- [ ] **Step 1: Write the test** `test_full_year_volumes_runtime_and_pareto` (markers `integration`, `slow`):
  - Run 365 days with seed 42, scale 1.0, `today = 2026-01-01`.
  - Within ±15%: customers 5,000; orders 8,000; events (+ pending) 600,000; leads 6,000.
  - Runtime < 300 s.
  - The top 20% of buyers by Σ `order_total` of non-cancelled orders hold 70–85% of it.
- [ ] **Step 2:** Run `uv run pytest -m slow -v -s`. Expected: PASS. If volumes are off, tune the base rates (the class constants of the simulators), never the spec targets. If it's slow, profile `StoreSnapshotLoader.load` first; indexes on `orders(status)` and `events(user_id)` go into `RawSchema`.
- [ ] **Step 3:** Commit `test: full-year volume, runtime and Pareto check`.

---

### Task 11: dbt project, targets, schema naming and seeds

**Adds tooling:**
- Group `dbt` = dbt-core, dbt-postgres, sqlfluff, sqlfluff-templater-dbt.
- `.sqlfluff` (templater `dbt`, dialect `postgres`, project/profiles dir `dbt`), `.sqlfluffignore`, and a pre-commit sqlfluff-lint hook on `dbt/**/*.sql`.
- `Makefile` targets `deps`, `seed` (dbt via `cd dbt && DBT_PROFILES_DIR=. uv run dbt …`).
- `.gitignore` += `dbt/target/`, `dbt/target-prod/`, `dbt/dbt_packages/`, `dbt/logs/`.
- pytest `testpaths` += `tests`.

**Files:**
- Create `dbt/{dbt_project.yml,profiles.yml,packages.yml}`, `dbt/macros/generate_schema_name.sql`, `dbt/seeds/{country_codes.csv,channel_mapping.csv,_seeds.yml}`.
- Test: `tests/dbt/test_seeds.py`.

**Interfaces — Produces:**
- Project `sherpa`, profile `sherpa`. Targets `dev` (default), `prod`, `ci`: host, port, user (`SHERPA_ADMIN_USER`), password and dbname via `env_var()`; `threads: 4`.
- Vars: `tax_rate: 0.20`, `session_timeout_minutes: 30`, `date_spine_start: '2024-01-01'`, `date_spine_end: '2028-12-31'`.
- Folder configs: staging `+schema: staging`, `+materialized: view`; marts `+schema: marts`, `+materialized: table`; seeds `+schema: seeds`; snapshots `+schema: snapshots`.
- `generate_schema_name`:
  - no custom schema → `target.schema`
  - snapshot → `snapshots`
  - `dev` → `dev_<custom>`
  - else → `<custom>` verbatim
- **Seeds:**
  - `country_codes(raw_value, code, name, region, is_eu)`: one row per value `RealismRules` can emit, i.e. every code as itself and in lowercase, plus `UK`/`uk` → `GB`. Regions: `DACH, Western Europe, Southern Europe, Benelux, UK & Ireland`.
  - `channel_mapping(utm_source, utm_medium, marketing_channel)`: `google,cpc,paid_search`; `facebook,paid_social,paid_social`; `instagram,paid_social,paid_social`; `newsletter,email,email`; `awin,affiliate,affiliate`.
  - `column_types` pinned (text, except `is_eu boolean`).

- [ ] **Step 1: Write the failing test** `tests/dbt/test_seeds.py::test_every_noisy_country_variant_is_in_seed`: reads the CSV and checks every value from `RealismRules.noisy_country_variants()` plus every canonical code is a `raw_value`. Run it. Expected: FAIL (no CSV).
- [ ] **Step 2:** Write the project files and seeds. `_seeds.yml`:
  - descriptions for both seeds and every column
  - tests: `raw_value` unique + not_null; `region` accepted_values; `dbt_utils.unique_combination_of_columns` on `(utm_source, utm_medium)`; `marketing_channel` accepted_values (`direct, organic, paid_search, paid_social, email, affiliate, referral, app`)
- [ ] **Step 3:** `make deps && cd dbt && DBT_PROFILES_DIR=. uv run dbt debug && DBT_PROFILES_DIR=. uv run dbt parse && DBT_PROFILES_DIR=. uv run dbt build --select resource_type:seed`. Expected: all pass. Then `dbt seed --target prod --target-path target-prod` → schema `seeds` exists; the `dev` run → `dev_seeds`.
- [ ] **Step 4:** Re-run the pytest test. Expected: PASS. Commit `feat: dbt project, targets, schema naming and seeds`.

---

### Task 12: Shop, marketing and CRM sources and staging

**Files:**
- Create `dbt/models/staging/shop/{_shop__sources.yml,_shop__models.yml}` + 16 `stg_shop__*.sql`.
- Create `dbt/models/staging/marketing/{_marketing__sources.yml,_marketing__models.yml,stg_marketing__campaigns.sql,stg_marketing__ad_spend_daily.sql}`.
- Create `dbt/models/staging/crm/{_crm__sources.yml,_crm__models.yml,stg_crm__leads.sql,stg_crm__email_sends.sql}`.

**Interfaces — Produces** (column names later milestones rely on):
- **IDs:** `id` → `<entity>_id` (`customer_id`, `address_id`, `category_id`, `product_id`, `variant_id`, `order_id`, `order_item_id`, `status_change_id`, `payment_id`, `shipment_id`, `promotion_id`, `coupon_code_id`, `order_discount_id`, `return_id`, `return_item_id`, `refund_id`, `campaign_id`, `lead_id`, `email_send_id`).
- **Other renames:** `parent_id` → `parent_category_id`; `type` → `address_type`/`promotion_type`; `status` → `order_status`/`payment_status`/`return_status` (`status` stays in the history table); `reason` → `return_reason`/`refund_reason`; `source` → `lead_source`; `name` → `<entity>_name`.
- **Transformations:**
  - `stg_shop__customers`: `email = lower(trim(email))`; `country_code` mapped through `country_codes`; `is_test_account = is_test_account OR email LIKE '%@sqlsherpa-test.com'`; `is_deleted`; `signup_at = created_at`.
  - `stg_shop__addresses`: country mapping.
  - `stg_crm__leads`: email normalized.
  - Others: renames only. No joins except to seeds.
- **Sources:** one per schema, with every table and column described.
  - Freshness: `shop.orders` (`updated_at`) and `crm.leads` (`updated_at`) warn 36 h / error 72 h; `marketing.ad_spend_daily` on `spend_date::timestamp` warn 48 h; all others `freshness: null`.
- **Tests:**
  - `unique` + `not_null` on every `<entity>_id`
  - `relationships` for every FK (incl. `promotions.campaign_id → stg_marketing__campaigns`, `refunds.payment_id → stg_shop__payments`)
  - `relationships` from both `country_code` columns to `ref('country_codes').code`
  - `accepted_values` on every enum column with exactly the StrEnum values
  - `unique_combination_of_columns` on `(spend_date, campaign_id)`
  - `dbt_utils.expression_is_true` for the order-total formula

- [ ] **Step 1:** `make reset && make generate DAYS=60`.
- [ ] **Step 2:** Write the sources YAML. `uv run dbt source freshness`. Expected: PASS.
- [ ] **Step 3:** Write the models YAML (descriptions + tests) first; `uv run dbt build --select staging.shop staging.marketing staging.crm`. Expected: FAIL (models missing).
- [ ] **Step 4:** Write the 20 models (CTEs: `source` → `renamed` → final `select`). Re-run. Expected: all PASS.
- [ ] **Step 5:** Explore once:
  - `uv run dbt show --select stg_shop__customers --limit 5`: lowercase emails, ISO codes
  - `uv run dbt ls --select +stg_shop__order_items`
  - `uv run dbt compile --select stg_shop__orders`: inspect how `source()` resolves
- [ ] **Step 6:** `uv run sqlfluff lint dbt/models` clean (fix the SQL; don't disable rules globally). Commit `feat: shop, marketing and crm sources and staging`.

---

### Task 13: Events source and staging

**Files:** Create `dbt/models/staging/events/{_events__sources.yml,_events__models.yml,stg_events__events.sql}`, `dbt/tests/assert_events_occurred_at_utc_parsing.sql`.

**Interfaces — Produces** `stg_events__events`: `event_id, anonymous_id, customer_id (bigint from user_id), event_type, occurred_at (timestamptz, UTC), received_at, device_type, utm_source, utm_medium, utm_campaign, referrer, page_path, product_id, variant_id, order_id, cart_value (numeric(12,2))`.
- Source freshness on `received_at`: warn 36 h / error 72 h.
- `occurred_at::timestamptz` (the offset is honoured); JSON extraction with `->>` + casts.
- Dedupe with `row_number() over (partition by event_id order by received_at) = 1`. View.

- [ ] **Step 1:** Write the YAML:
  - descriptions for every column
  - tests: `event_id` unique + not_null; `anonymous_id` not_null; `event_type` accepted_values (8); `customer_id` → `stg_shop__customers`; `order_id` → `stg_shop__orders` with `where: "order_id is not null"`
- [ ] **Step 2:** Singular test: the literals `2025-01-15T10:00:00+01:00`, `2025-07-15T11:00:00+02:00`, `2025-07-15T09:00:00+00:00` must all equal 09:00 UTC on their dates (the test returns the mismatching rows).
- [ ] **Step 3:** `uv run dbt build --select staging.events assert_events_occurred_at_utc_parsing`. Expected: FAIL. Write the model. Re-run. Expected: PASS, including `unique(event_id)`, which proves the R1 dedupe.
- [ ] **Step 4:** Commit `feat: events source and staging`.

---

### Task 14: dim_date, docs, coverage gate and data-platform-1 acceptance

**Adds tooling:**
- `Makefile` targets `build`, `build-prod`, `docs`, `docs-prod`, `docs-check`, `lint`, `test`.
- `reset` extended to drop the dbt schemas (`dev_seeds, dev_staging, dev_marts, seeds, staging, marts`) via `docker compose exec postgres psql -U sherpa_admin -d sherpa`.

**Files:**
- Create `dbt/models/marts/shared/{dim_date.sql,_shared__models.yml}`, `dbt/models/overview.md`, `scripts/check_docs_coverage.py`.
- Test: `tests/scripts/test_check_docs_coverage.py`.

**Interfaces — Produces:**
- `dim_date`: `date_day, week_start (Monday), month_start, quarter, year, day_of_week_name, is_weekend, is_black_friday_week`, via `dbt_utils.date_spine` between the `date_spine_*` vars. `meta: {domain: shared, grain: "one row per calendar day"}`.
- `scripts/check_docs_coverage.py`, a class `DocsCoverageChecker(manifest: dict, catalog: dict)`:
  - `undocumented(path_prefix: str) -> list[str]` (`"<model>"` / `"<model>.<column>"`, including catalog columns missing from the YAML)
  - `main()`: CLI (`--target-path`, `--path`); exits 1 and prints the list when it isn't empty

- [ ] **Step 1: Write failing tests** `TestDocsCoverageChecker`:
  - `test_reports_model_without_description`
  - `test_reports_catalog_column_missing_from_yaml`
  - `test_reports_blank_description_as_missing` (whitespace only)
  - `test_ignores_models_outside_prefix`
  - `test_clean_project_returns_empty`

  Run (FAIL) → implement → run (PASS).
- [ ] **Step 2:** Write `dim_date` + YAML (`date_day` unique + not_null) and `overview.md` (`__overview__`: the project, the four source systems, the layers, how to navigate the docs).
- [ ] **Step 3:** `uv run dbt build --select dim_date`, then `uv run dbt show --inline "select date_day, is_black_friday_week from {{ ref('dim_date') }} where date_day in ('2025-11-23','2025-11-24','2025-11-30','2025-12-01')"`. Expected: `false, true, true, false`.
- [ ] **Step 4:** Makefile:
  - `build` = `dbt source freshness && dbt build`; `build-prod` = the same with `--target prod --target-path target-prod`
  - `docs` / `docs-prod` = generate + serve
  - `docs-check` = `dbt docs generate` + the checker on `models/staging`
  - `lint` = ruff check + format check + mypy + sqlfluff
  - `test` = `pytest`
- [ ] **Step 5: Acceptance (data-platform-1),** on a fresh volume: `make down && docker volume rm <project>_pgdata && make up && make backfill DAYS=365 && make build && make docs-check && make build-prod`. Expected:
  - the generator finishes in under 5 min
  - freshness reports no errors
  - `dbt build` all PASS
  - `docs-check` prints nothing
  - prod schemas `seeds, staging, marts` exist and `dbt/target-prod/manifest.json` exists
- [ ] **Step 6:** `make lint && make test`. Expected: clean, all non-slow tests PASS.
- [ ] **Step 7:** Commit `feat: dim_date, docs overview, docs coverage gate and make targets`.

---

### Task 15: Documentation and how-to guides

**Files:**
- Create `docs/guides/basic/generator-cli.md`, `docs/guides/basic/building-the-warehouse.md`, `docs/guides/advanced/extending-the-source-data-model.md`.
- Modify `README.md`, `CLAUDE.md`.

All docs describe the current state only (CLAUDE.md › Source of truth). Update every part this milestone made true or changed, and nothing it didn't. Every command in a guide must be run once while writing it, and its output must match what the guide shows.

- [ ] **Step 1: `docs/guides/basic/generator-cli.md`:** using the generator, easy first, then complete.
  - **Prerequisites and setup:** `.env` from `.env.example`, `make up`.
  - **First run:** `make backfill DAYS=365`, and what it creates (schemas, rough row counts, ~5 min).
  - **Every CLI command and option:** `generate --days/--start-date/--scale/--seed`, `status`, `reset`, each with a real example and its output. Include the Make shortcuts.
  - **Common recipes:**
    - a small, fast dataset for experiments (`--scale 0.1 --days 30`)
    - a reproducible dataset (fixed `--seed` and `--start-date`)
    - topping up to yesterday after a few days off
    - starting over
  - **What the errors mean and how to fix them:** seed/scale mismatch, start date after yesterday, nothing to simulate, Postgres not running.
  - **Inspecting the data with `psql`:** a handful of example queries per source system, including one that shows each realism rule (a duplicate event, a noisy email, a test account, a split payment).
- [ ] **Step 2: `docs/guides/basic/building-the-warehouse.md`:** using dbt on the generated data.
  - dev vs prod targets, and where each one writes (`dev_*` schemas vs plain ones, `target/` vs `target-prod/`)
  - `make deps`, `make seed`, `make build`, `make build-prod`; `dbt source freshness`
  - running a single model or test (`dbt build --select …`); `dbt show`, `dbt ls`, `dbt compile`
  - `make docs` and how to read the lineage graph
  - what a failing test looks like and how to investigate it
- [ ] **Step 3: `docs/guides/advanced/extending-the-source-data-model.md`:** changing the source model end to end. It has one worked example per kind of change, each with the exact files in order and the tests that prove it:
  1. **Add a column to an existing table** (e.g. `customers.preferred_language`):
     - the record field in `records/`, the DDL in `RawSchema`
     - the simulator that sets the value
     - the source YAML, the staging model + its tests, and an `accepted_values` test if it's an enum
     - then `make reset && make backfill`
  2. **Add a new table** (e.g. `raw_shop.wishlist_items`):
     - a new record class, its DDL, a `PrimaryKeyAllocator` key
     - a new behaviour simulator wired into `Simulator` (and where in the day order it goes, and why)
     - the `StoreSnapshot` fields it needs
     - the source + staging model + tests
  3. **Add or change an enum value** (e.g. a new `PaymentMethod`): the StrEnum, the generator weights, and the staging `accepted_values` test that must change with it.
  4. **Add or tune a realism rule:**
     - the constant in `RealismRules`, the rule method and its rate test
     - the staging logic that cleans it up
     - and why rules are kept to the fixed list (data platform spec §4.4)
  5. **Change volumes or rates:** which class constants to change, and how to verify with the full-year volume test (`pytest -m slow`).
  6. **Keep determinism intact:** the rules every change must respect (`DeterministicRandom` only, no `hash()`, keys from `PrimaryKeyAllocator`, `entity_fraction` for future decisions). Explain how to prove it with the resume-equals-single-run test.
- [ ] **Step 4: `README.md`:**
  - **Status line:** data-platform-1 is done; data-platform-2 is next.
  - **Quickstart:** add one now that the commands exist. Prerequisites: Docker, uv, GNU Make. Commands: `cp .env.example .env`, `make up`, `make backfill DAYS=365`, `make build`, `make docs`.
  - **Repository layout:** `generator/`, `dbt/`, `docker/`, `scripts/`, `docs/`, each with one line.
  - **Docs:** keep the link to this plan, and add a "Guides" list linking the three guides.

- [ ] **Step 5: `CLAUDE.md`:**
  - Add a **Commands** section listing exactly the Makefile targets that exist after Task 14 (`up`, `down`, `generate`, `backfill`, `status`, `reset`, `deps`, `seed`, `build`, `build-prod`, `docs`, `docs-prod`, `docs-check`, `lint`, `test`, `mutate`), one line each.
  - Check that the module and class names cited in Python Guidelines › Structure match the code.
  - Point "Current plan" at the next plan once it exists; until then, say data-platform-2 is to be planned.
- [ ] **Step 6:** Check `CLAUDE.md`, `README.md` and the three guides for every command, path, module and class name they mention, and confirm each one exists (`grep` + `ls`). Expected: no dangling references.
- [ ] **Step 7:** Commit `docs: how-to guides, README and CLAUDE.md for data-platform-1`.
