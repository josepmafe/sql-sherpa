import type { components } from "../api/schema";

export type QueryResponse = components["schemas"]["QueryResponse"];
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

// Server mode wraps the SQL in a subquery, which duplicate column names would make ambiguous (UI spec §5).
export function hasDuplicateColumns(columns: readonly string[]): boolean {
  return new Set(columns).size !== columns.length;
}

export function isServerSide(response: QueryResponse): boolean {
  return response.mode === "server" || (response.truncated && !hasDuplicateColumns(response.columns));
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

export function cellText(value: Cell): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
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
  if (operator === "contains") return (cellText(value) ?? "").toLowerCase().includes(filter.value.toLowerCase());
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

// NaN means "not comparable" (a non-numeric filter value on a numeric column): it matches nothing.
function compareValues(left: Cell, right: Cell, type: ColumnType): number {
  switch (type) {
    case "number":
      return numeric(Number(left), Number(right));
    case "date":
    case "timestamp":
      return numeric(parseTime(left), parseTime(right));
    case "boolean":
      return numeric(Number(left === true || left === "true"), Number(right === true || right === "true"));
    default:
      return String(cellText(left)).localeCompare(String(cellText(right)));
  }
}

// Unparseable values (not expected from the backend) sort after parseable ones, by text, so the order stays total.
function sortOrder(left: Cell, right: Cell, type: ColumnType): number {
  const order = compareValues(left, right, type);
  if (!Number.isNaN(order)) return order;
  const leftOk = !Number.isNaN(compareValues(left, left, type));
  const rightOk = !Number.isNaN(compareValues(right, right, type));
  if (leftOk !== rightOk) return leftOk ? -1 : 1;
  return String(cellText(left)).localeCompare(String(cellText(right)));
}

// The backend sends isoformat(): "2025-01-15", "2025-01-15T10:30:00" (timestamp, no offset) or
// "2025-01-15T10:30:00.123456+00:00" (timestamptz). A bare timestamp is read as UTC, not browser-local, so
// it compares consistently with dates.
const NO_OFFSET = /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;
function parseTime(value: Cell): number {
  const text = String(value);
  return Date.parse(NO_OFFSET.test(text) ? `${text}Z` : text);
}

function numeric(left: number, right: number): number {
  if (Number.isNaN(left) || Number.isNaN(right)) return Number.NaN;
  return left === right ? 0 : left < right ? -1 : 1;
}
