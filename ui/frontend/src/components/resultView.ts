import type { components } from "../api/schema";

export type QueryResponse = components["schemas"]["QueryResponse"];
type CatalogTree = components["schemas"]["CatalogTree"];
export type ColumnType = QueryResponse["column_types"][number];
export type Operator = components["schemas"]["FilterSpec"]["operator"];
export type Cell = unknown;
export type Row = Cell[];

// Sort and filters refer to columns by position: a result may repeat a column name.
export type SortState = { index: number; direction: "asc" | "desc" };
export type FilterState = { index: number; operator: Operator; value: string };
export type ResultView = { sort?: SortState; filters: FilterState[] };

export const EMPTY_VIEW: ResultView = { filters: [] };
export const VALUELESS_OPERATORS: readonly Operator[] = ["is_null", "is_not_null"];

// The lower-cased names of the catalog's PII columns. The backend masks a result column by its name in any case, and
// refuses to sort or filter by it on the server (UI spec §5), so the grid offers neither.
export function maskedColumnNames(catalog: CatalogTree | undefined): ReadonlySet<string> {
  const tables = catalog?.domains.flatMap((domain) => domain.tables) ?? [];
  return new Set(tables.flatMap((table) => table.columns.filter((c) => c.is_pii).map((c) => c.name.toLowerCase())));
}

// Server mode wraps the SQL in a subquery, which duplicate column names would make ambiguous (UI spec §5).
export function hasDuplicateColumns(columns: readonly string[]): boolean {
  return new Set(columns).size !== columns.length;
}

// Whether sorting and filtering `view` goes to the server (UI spec §5): the browser cannot reproduce Postgres's order
// or text for an `other` column (interval, uuid, json, arrays, ...), nor `contains` on a number (JavaScript and
// Postgres write a float differently).
export function needsServer(view: ResultView, types: readonly ColumnType[]): boolean {
  const typeAt = (index: number): ColumnType => types[index] ?? "other";
  const sortsOther = view.sort !== undefined && typeAt(view.sort.index) === "other";
  const filtersServerOnly = view.filters.some(
    (f) => typeAt(f.index) === "other" || (f.operator === "contains" && typeAt(f.index) === "number"),
  );
  return sortsOther || filtersServerOnly;
}

// Whether `response` holds the rows of the sorted and filtered view already: a server answer, a truncated result
// (the browser holds only a part of the rows), or a complete one that the browser cannot sort or filter exactly.
export function isServerSide(response: QueryResponse, view: ResultView): boolean {
  if (response.mode === "server") return true;
  if (hasDuplicateColumns(response.columns)) return false;
  return response.truncated || needsServer(view, response.column_types);
}

export function toServerOptions(view: ResultView, columns: readonly string[]) {
  return {
    sort: view.sort ? [{ column: columns[view.sort.index] ?? "", direction: view.sort.direction }] : [],
    filters: view.filters.map((f) => ({
      column: columns[f.index] ?? "",
      operator: f.operator,
      value: VALUELESS_OPERATORS.includes(f.operator) ? null : f.value,
    })),
  };
}

// The same NULL semantics as Postgres, which server mode uses: ascending puts NULLs last, descending first;
// every comparison with NULL (including `!=`) is unknown, so those rows are dropped.
export function applyView(rows: readonly Row[], types: readonly ColumnType[], view: ResultView): Row[] {
  const kept = rows.filter((row) => view.filters.every((f) => matches(row[f.index], types[f.index] ?? "other", f)));
  const { sort } = view;
  if (!sort) return kept;
  const type = types[sort.index] ?? "other";
  const sign = sort.direction === "asc" ? 1 : -1;
  return kept
    .map((row, position) => ({ row, position }))
    .sort((a, b) => {
      const left = a.row[sort.index];
      const right = b.row[sort.index];
      if (left == null || right == null) return nullOrder(left, right, sign) || a.position - b.position;
      return sign * sortOrder(left, right, type) || a.position - b.position;
    })
    .map(({ row }) => row);
}

// Why Postgres would refuse a filter value for the column's type, if it would: server mode sends the value as a bound
// parameter, and an unreadable one fails the whole re-run. `contains` matches text, so any value goes.
export function filterValueProblem(type: ColumnType, operator: Operator, value: string): string | undefined {
  if (operator === "contains" || VALUELESS_OPERATORS.includes(operator)) return undefined;
  switch (type) {
    case "number":
      return Number.isNaN(parseNumber(value)) ? "Enter a number, like 12.5" : undefined;
    case "date":
    case "timestamp":
      return Number.isNaN(parseTime(value)) ? "Enter a date, like 2025-01-15 or 2025-01-15 10:30" : undefined;
    case "boolean":
      return parseBoolean(value) === undefined ? "Enter true or false" : undefined;
    default:
      return undefined;
  }
}

// A cell as Postgres's `::text` writes it, which is what server mode's `contains` matches: jsonb as `{"a": 1}` and
// a timestamp as `2025-01-15 08:30:00.12+00`. A Postgres array arrives as JSON too, so it reads `[1, 2]`, not `{1,2}`.
export function cellText(value: Cell, type?: ColumnType): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return type === "timestamp" ? postgresTimestamp(value) : value;
  if (typeof value === "object") return postgresJson(value);
  return String(value);
}

function nullOrder(left: Cell, right: Cell, sign: number): number {
  if (left == null && right == null) return 0;
  return (left == null ? 1 : -1) * sign;
}

function matches(value: Cell, type: ColumnType, filter: FilterState): boolean {
  const { operator } = filter;
  if (operator === "is_null") return value == null;
  if (operator === "is_not_null") return value != null;
  if (value == null) return false;
  if (operator === "contains") return (cellText(value, type) ?? "").toLowerCase().includes(filter.value.toLowerCase());
  const order = compareValues(value, filter.value, type);
  if (Number.isNaN(order)) return false;
  switch (operator) {
    case "=":
      return order === 0;
    case "!=":
      return order !== 0;
    case "<":
      return order < 0;
    case "<=":
      return order <= 0;
    case ">":
      return order > 0;
    case ">=":
      return order >= 0;
  }
}

// NaN means "not comparable" (a value Postgres would not read as the column's type): it matches nothing.
function compareValues(left: Cell, right: Cell, type: ColumnType): number {
  switch (type) {
    case "number":
      return compareNumbers(left, right);
    case "date":
      return numeric(parseDatePart(left), parseDatePart(right));
    case "timestamp":
      return numeric(parseTime(left), parseTime(right));
    case "boolean":
      return numeric(booleanNumber(left), booleanNumber(right));
    default:
      return compareCodePoints(cellText(left) ?? "", cellText(right) ?? "");
  }
}

// Unparseable values (not expected from the backend) sort after parseable ones, by text, so the order stays total.
function sortOrder(left: Cell, right: Cell, type: ColumnType): number {
  const order = compareValues(left, right, type);
  if (!Number.isNaN(order)) return order;
  const leftOk = !Number.isNaN(compareValues(left, left, type));
  const rightOk = !Number.isNaN(compareValues(right, right, type));
  if (leftOk !== rightOk) return leftOk ? -1 : 1;
  return compareCodePoints(cellText(left) ?? "", cellText(right) ?? "");
}

// The deployed Postgres (postgres:16-alpine, musl) collates bytewise, which for UTF-8 is code point order. JavaScript's
// `<` compares UTF-16 units (an emoji would sort before U+FF5E) and localeCompare is ICU's order ("a" before "B").
function compareCodePoints(left: string, right: string): number {
  const length = Math.min(left.length, right.length);
  for (let i = 0; i < length; i++) {
    const a = left.codePointAt(i) ?? 0;
    const b = right.codePointAt(i) ?? 0;
    if (a !== b) return a < b ? -1 : 1;
    if (a > 0xffff) i++; // both strings hold the same surrogate pair here
  }
  return numeric(left.length, right.length);
}

// Postgres's numeric input: a decimal with an optional exponent, spaces around it allowed. Number() would read
// "" and "  " as 0 and "0x10" as 16.
const NUMBER_TEXT = /^\s*([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?\s*$/i;
function parseNumber(value: Cell): number {
  if (typeof value === "number") return value;
  const text = String(value);
  return NUMBER_TEXT.test(text) ? Number(text) : Number.NaN;
}

// A JSON number is a double, so it compares as one (a float8 against Postgres's own double parse of the filter). A
// string is an int8 past 2^53 or a numeric, which a double would round: those compare as digits.
function compareNumbers(left: Cell, right: Cell): number {
  if (typeof left === "number") return numeric(left, parseNumber(right));
  return compareDecimals(parseDecimal(String(left)), parseDecimal(String(right)));
}

// value = 0.<digits> x 10^magnitude, with no leading or trailing zeros in `digits` (zero has none).
type Decimal = { negative: boolean; digits: string; magnitude: number };
function parseDecimal(text: string): Decimal | undefined {
  const match = NUMBER_TEXT.exec(text);
  if (!match) return undefined;
  const [, sign = "", mantissa = "", exponent = "0"] = match;
  const [whole = "", fraction = ""] = mantissa.split(".");
  const all = whole + fraction;
  const significant = all.replace(/^0+/, "");
  const leadingZeros = all.length - significant.length;
  return {
    negative: sign === "-",
    digits: significant.replace(/0+$/, ""),
    magnitude: whole.length - leadingZeros + Number(exponent),
  };
}

function compareDecimals(left: Decimal | undefined, right: Decimal | undefined): number {
  if (!left || !right) return Number.NaN;
  const sign = (d: Decimal) => (d.digits === "" ? 0 : d.negative ? -1 : 1);
  if (sign(left) !== sign(right)) return sign(left) < sign(right) ? -1 : 1;
  if (sign(left) === 0) return 0;
  const order = compareMagnitudes(left, right);
  return sign(left) < 0 ? -order : order;
}

function compareMagnitudes(left: Decimal, right: Decimal): number {
  if (left.magnitude !== right.magnitude) return left.magnitude < right.magnitude ? -1 : 1;
  const width = Math.max(left.digits.length, right.digits.length);
  return compareCodePoints(left.digits.padEnd(width, "0"), right.digits.padEnd(width, "0"));
}

// Postgres's boolean input: true/yes/on/1 and false/no/off/0 in any case, with spaces around, and any unique prefix
// ("t", "fa"; "o" alone is ambiguous). The API sends JSON booleans; a string cell is read the same way.
const BOOLEAN_WORDS: readonly [string, boolean][] = [
  ["true", true],
  ["yes", true],
  ["false", false],
  ["no", false],
];
function parseBoolean(value: Cell): boolean | undefined {
  if (typeof value === "boolean") return value;
  const text = String(value).trim().toLowerCase();
  if (text === "1" || text === "on") return true;
  if (text === "0" || text === "of" || text === "off") return false;
  if (text === "") return undefined;
  return BOOLEAN_WORDS.find(([word]) => word.startsWith(text))?.[1];
}

function booleanNumber(value: Cell): number {
  const parsed = parseBoolean(value);
  return parsed === undefined ? Number.NaN : Number(parsed);
}

// A date, or a date and time with a `T` (the API's isoformat) or a space (what Postgres prints and people type), an
// optional fraction and an optional offset (Z, +02, +0200, +02:00). No offset means UTC, the server session's zone,
// never browser-local time.
const TIME_TEXT = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}(?::?\d{2})?)?)?$/i;
const MS_PER_MINUTE = 60_000;
function parseTime(value: Cell): number {
  const match = TIME_TEXT.exec(String(value).trim());
  if (!match) return Number.NaN;
  const [, year, month, day, hour = "0", minute = "0", second = "0", fraction = "0", offset = "Z"] = match;
  const utc = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second));
  // Date.UTC rolls 2025-02-30 over into March and 10:75 into 11:15; Postgres rejects both.
  const time = new Date(utc);
  if (time.getUTCMonth() !== Number(month) - 1 || time.getUTCDate() !== Number(day)) return Number.NaN;
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return Number.NaN;
  return utc + Number(fraction) * 1000 - offsetMinutes(offset) * MS_PER_MINUTE;
}

// Postgres reads a `date` column's filter value by its date part, whatever time or offset follows it.
const DATE_LENGTH = "2025-01-15".length;
function parseDatePart(value: Cell): number {
  const text = String(value).trim();
  return Number.isNaN(parseTime(text)) ? Number.NaN : parseTime(text.slice(0, DATE_LENGTH));
}

function offsetMinutes(offset: string): number {
  if (offset.toUpperCase() === "Z") return 0;
  const digits = offset.slice(1).replace(":", "");
  const minutes = Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2) || "0");
  return offset.startsWith("-") ? -minutes : minutes;
}

// isoformat() to Postgres's text form: a space for the `T`, the fraction without trailing zeros, `+00` for `+00:00`.
const ISO_TIMESTAMP = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:([+-]\d{2}):(\d{2}))?$/;
function postgresTimestamp(text: string): string {
  const match = ISO_TIMESTAMP.exec(text);
  if (!match) return text;
  const [, date, time, fraction = "", hours, minutes] = match;
  const digits = fraction.replace(/0+$/, "");
  const offset = hours === undefined ? "" : minutes === "00" ? hours : `${hours}:${minutes}`;
  return `${date} ${time}${digits === "" ? "" : `.${digits}`}${offset}`;
}

// jsonb's text form: `", "` between items and `": "` after keys. The keys keep the order the API sent, which is
// jsonb's own. A number keeps JavaScript's form (`2`), where jsonb may print `2.0`.
function postgresJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(postgresJson).join(", ")}]`;
  if (value !== null && typeof value === "object") {
    const members = Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}: ${postgresJson(item)}`);
    return `{${members.join(", ")}}`;
  }
  return JSON.stringify(value);
}

function numeric(left: number, right: number): number {
  if (Number.isNaN(left) || Number.isNaN(right)) return Number.NaN;
  return left === right ? 0 : left < right ? -1 : 1;
}
