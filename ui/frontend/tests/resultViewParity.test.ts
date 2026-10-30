import { describe, expect, it } from "vitest";

import parity from "../../parity/sort_filter_cases.json";
import {
  applyView,
  type ColumnType,
  filterValueProblem,
  needsServer,
  type Operator,
  type ResultView,
  type Row,
} from "../src/components/resultView";

// The case file is shared with the backend (ui/backend/tests/readonly/test_sort_filter_parity.py), which checks its
// expected ids against real Postgres. Here the browser's code has to give the same rows (UI spec §5).
type ParityTable = { name: string; columns: string[]; column_types: ColumnType[]; rows: Row[] };
type ParityCase = {
  name: string;
  table: string;
  sort?: { index: number; direction: "asc" | "desc" };
  filters?: { index: number; operator: Operator; value?: string }[];
  expected_ids?: number[];
  expected_error?: boolean;
  route?: "server";
  accepted_difference?: string;
  client_expected_ids?: number[];
};
const { tables, cases } = parity as unknown as { tables: ParityTable[]; cases: ParityCase[] };
const TABLES = new Map(tables.map((table) => [table.name, table]));

const viewOf = (c: ParityCase): ResultView => ({
  sort: c.sort,
  filters: (c.filters ?? []).map((f) => ({ index: f.index, operator: f.operator, value: f.value ?? "" })),
});
const tableOf = (c: ParityCase): ParityTable => {
  const table = TABLES.get(c.table);
  if (!table) throw new Error(`unknown table ${c.table}`);
  return table;
};
const ids = (rows: Row[]) => rows.map((row) => row[0]);

describe("the browser's sort and filter against the shared cases", () => {
  it.each(cases.map((c) => [c.name, c] as const))("%s: routes to the server only when the case says so", (_name, c) => {
    expect(needsServer(viewOf(c), tableOf(c).column_types)).toBe(c.route === "server");
  });

  const inBrowser = cases.filter((c) => c.route !== "server");

  it.each(inBrowser.filter((c) => !c.expected_error).map((c) => [c.name, c] as const))(
    "%s: gives the rows Postgres gives",
    (_name, c) => {
      const table = tableOf(c);

      const rows = applyView(table.rows, table.column_types, viewOf(c));

      expect(ids(rows)).toEqual(c.accepted_difference ? c.client_expected_ids : c.expected_ids);
    },
  );

  it.each(inBrowser.filter((c) => c.expected_error).map((c) => [c.name, c] as const))(
    "%s: refuses a value Postgres refuses, and matches nothing",
    (_name, c) => {
      const table = tableOf(c);
      const view = viewOf(c);

      const problems = view.filters.map((f) => filterValueProblem(table.column_types[f.index] ?? "other", f.operator, f.value));

      expect(problems.some((problem) => problem !== undefined)).toBe(true);
      expect(applyView(table.rows, table.column_types, view)).toEqual([]);
    },
  );
});
