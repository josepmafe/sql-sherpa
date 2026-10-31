import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryResponse } from "./fixtures/api";
import {
  applyView,
  cellText,
  EMPTY_VIEW,
  filterValueProblem,
  hasDuplicateColumns,
  isServerSide,
  needsServer,
  toServerOptions,
} from "../src/components/resultView";

const ids = (rows: unknown[][]) => rows.map((r) => r[0]);

describe("applyView sorting", () => {
  const rows = [
    ["a", "10"],
    ["b", null],
    ["c", "9"],
    ["d", "2.5"],
  ];

  it("compares numeric strings as numbers, not text", () => {
    const sorted = applyView(rows, ["text", "number"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["d", "c", "a", "b"]);
  });

  it("puts NULLs last ascending and first descending, like Postgres", () => {
    const desc = applyView(rows, ["text", "number"], { sort: { index: 1, direction: "desc" }, filters: [] });

    expect(ids(desc)).toEqual(["b", "a", "c", "d"]);
  });

  it("sorts dates chronologically and keeps ties in their original order", () => {
    const dated = [
      ["x", "2025-03-01"],
      ["y", "2025-01-15"],
      ["z", "2025-01-15"],
    ];

    const sorted = applyView(dated, ["text", "date"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["y", "z", "x"]);
  });
});

describe("applyView timestamps", () => {
  it("sorts real backend timestamptz strings (offsets, microseconds) chronologically", () => {
    const rows = [
      ["late", "2025-01-15T12:00:00.123456+00:00"],
      ["early", "2025-01-15T12:00:00.000001+00:00"],
      ["offset", "2025-01-15T12:30:00+02:00"],
    ];

    const sorted = applyView(rows, ["text", "timestamp"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["offset", "early", "late"]);
  });

  it("reads a bare timestamp as UTC, consistently with a date filter", () => {
    const rows = [
      ["in", "2025-01-15T23:30:00"],
      ["out", "2025-01-14T23:30:00"],
    ];

    const kept = applyView(rows, ["text", "timestamp"], { filters: [{ index: 1, operator: ">=", value: "2025-01-15" }] });

    expect(ids(kept)).toEqual(["in"]);
  });

  describe("in a browser that is not on UTC", () => {
    beforeEach(() => vi.stubEnv("TZ", "America/New_York"));
    afterEach(() => vi.unstubAllEnvs());

    it("reads a typed timestamp with a space and no offset as UTC, like the server's session", () => {
      const rows = [
        ["bare", "2025-01-15T10:30:00"],
        ["tz", "2025-01-15T10:30:00+00:00"],
        ["other", "2025-01-15T09:30:00+00:00"],
      ];
      const equal = (value: string) =>
        ids(applyView(rows, ["text", "timestamp"], { filters: [{ index: 1, operator: "=", value }] }));

      expect(equal("2025-01-15 10:30:00")).toEqual(["bare", "tz"]);
      expect(equal("2025-01-15 10:30")).toEqual(["bare", "tz"]);
      expect(equal("2025-01-15 10:30:00+00")).toEqual(["bare", "tz"]);
      expect(equal("2025-01-15 11:30:00+01:00")).toEqual(["bare", "tz"]);
    });
  });

  it("sorts unparseable values after valid ones instead of leaving the order undefined", () => {
    const rows = [
      ["bad", "garbage"],
      ["ok", "2025-01-15"],
    ];

    const sorted = applyView(rows, ["text", "date"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["ok", "bad"]);
  });
});

describe("applyView booleans", () => {
  // The API sends JSON booleans; string cells are read like typed values.
  const rows = [
    ["a", true],
    ["b", false],
    ["c", null],
    ["d", "t"],
    ["e", "f"],
  ];
  const equal = (value: string) =>
    ids(applyView(rows, ["text", "boolean"], { filters: [{ index: 1, operator: "=", value }] }));

  it("reads filter values like Postgres's boolean input", () => {
    for (const value of ["t", "TRUE", "1", "yes", "On", " true "]) expect(equal(value)).toEqual(["a", "d"]);
    for (const value of ["f", "False", "0", "no", "OFF"]) expect(equal(value)).toEqual(["b", "e"]);
  });

  it("reads the unique prefixes of off, so 'of' is false and 'o' is nothing", () => {
    expect(equal("of")).toEqual(["b", "e"]);
    expect(filterValueProblem("boolean", "=", "of")).toBeUndefined();
    expect(filterValueProblem("boolean", "=", "o")).toBeDefined();
  });

  it("matches nothing for a value Postgres would not read as a boolean", () => {
    for (const value of ["maybe", "o", "2"]) expect(equal(value)).toEqual([]);
  });

  it("sorts false before true, NULL last", () => {
    const sorted = applyView(rows, ["text", "boolean"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["b", "e", "a", "d", "c"]);
  });
});

describe("filter values Postgres would refuse", () => {
  const dated = [
    ["a", "2025-03-01"],
    ["b", "2025-03-02"],
  ];
  const stamped = [
    ["a", "2025-01-15T10:15:00+00:00"],
    ["b", "2025-01-15T11:15:00+00:00"],
  ];

  it.each([
    ["date", "2025-02-30"],
    ["date", "2025-13-01"],
    ["timestamp", "2025-02-30 10:00"],
    ["timestamp", "2025-01-15 10:75"],
    ["timestamp", "2025-01-15 24:00"],
    ["timestamp", "2025-01-15 10:30:61"],
  ] as const)("a %s filter of %s is refused", (type, value) => {
    expect(filterValueProblem(type, "=", value)).toMatch(/^Enter a date/);
  });

  it.each([
    ["date", "2025-02-28"],
    ["date", "2024-02-29"],
    ["timestamp", "2025-01-15 23:59:59"],
  ] as const)("a %s filter of %s is accepted", (type, value) => {
    expect(filterValueProblem(type, "=", value)).toBeUndefined();
  });

  it("an impossible date matches nothing instead of rolling over into the next month", () => {
    const kept = applyView(dated, ["text", "date"], { filters: [{ index: 1, operator: "=", value: "2025-02-30" }] });

    expect(kept).toEqual([]);
  });

  it("an impossible time matches nothing instead of rolling over into the next hour", () => {
    const kept = applyView(stamped, ["text", "timestamp"], {
      filters: [{ index: 1, operator: "=", value: "2025-01-15 10:75" }],
    });

    expect(kept).toEqual([]);
  });
});

describe("applyView text", () => {
  const rows = [["a"], ["B"], ["é"], ["Z"], ["😀"], ["～"]];

  it("orders by code point, like the deployed Postgres (musl, bytewise), not by locale", () => {
    const sorted = applyView(rows, ["text"], { sort: { index: 0, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["B", "Z", "a", "é", "～", "😀"]);
  });

  it("compares filter values by code point too", () => {
    const kept = applyView(rows, ["text"], { filters: [{ index: 0, operator: "<", value: "a" }] });

    expect(ids(kept)).toEqual(["B", "Z"]);
  });
});

describe("applyView contains", () => {
  const contains = (rows: unknown[][], type: "other" | "timestamp", value: string) =>
    ids(applyView(rows, ["text", type], { filters: [{ index: 1, operator: "contains", value }] }));

  it("matches jsonb in Postgres's text form, as the server's ::text does", () => {
    const rows = [["j", { a: 1, b: [1, 2] }]];

    expect(contains(rows, "other", '"a": 1, "b": [1, 2]')).toEqual(["j"]);
    expect(contains(rows, "other", '"a":1')).toEqual([]);
  });

  it("matches timestamps in Postgres's text form, with a space and no trailing zeros", () => {
    const rows = [
      ["t", "2025-01-15T08:30:00.120000+00:00"],
      ["u", "2025-01-15T10:30:00"],
    ];

    expect(contains(rows, "timestamp", "2025-01-15 08:30:00.12+00")).toEqual(["t"]);
    expect(contains(rows, "timestamp", "15 10:30")).toEqual(["u"]);
    expect(contains(rows, "timestamp", "T08")).toEqual([]);
    expect(contains(rows, "timestamp", "120000")).toEqual([]);
  });
});

describe("applyView filtering", () => {
  const rows = [
    ["a", "10", "Alpha"],
    ["b", null, null],
    ["c", "9", "beta"],
  ];
  const types = ["text", "number", "text"] as const;

  it("!= drops NULL rows, like SQL", () => {
    const kept = applyView(rows, types, { filters: [{ index: 1, operator: "!=", value: "10" }] });

    expect(ids(kept)).toEqual(["c"]);
  });

  it("compares numerically with <", () => {
    const kept = applyView(rows, types, { filters: [{ index: 1, operator: "<", value: "10" }] });

    expect(ids(kept)).toEqual(["c"]);
  });

  it("matches a non-numeric value on a number column to nothing", () => {
    const kept = applyView(rows, types, { filters: [{ index: 1, operator: "=", value: "abc" }] });

    expect(kept).toEqual([]);
  });

  it("matches a whitespace-only value on a number column to nothing, not to 0", () => {
    const zeros = [
      ["zero", "0"],
      ["ten", "10"],
    ];

    expect(applyView(zeros, types, { filters: [{ index: 1, operator: "=", value: " " }] })).toEqual([]);
    expect(applyView(zeros, types, { filters: [{ index: 1, operator: "<=", value: "   " }] })).toEqual([]);
  });

  it("contains is a case-insensitive substring and skips NULL", () => {
    const kept = applyView(rows, types, { filters: [{ index: 2, operator: "contains", value: "ALP" }] });

    expect(ids(kept)).toEqual(["a"]);
  });

  it("is_null and is_not_null split the rows", () => {
    const nulls = applyView(rows, types, { filters: [{ index: 2, operator: "is_null", value: "" }] });
    const values = applyView(rows, types, { filters: [{ index: 2, operator: "is_not_null", value: "" }] });

    expect(ids(nulls)).toEqual(["b"]);
    expect(ids(values)).toEqual(["a", "c"]);
  });

  it("returns nothing for empty input", () => {
    expect(applyView([], types, { filters: [{ index: 0, operator: "=", value: "a" }] })).toEqual([]);
  });
});

describe("helpers", () => {
  it("cellText renders NULL as null and JSON in Postgres's jsonb text form", () => {
    expect(cellText(null)).toBeNull();
    expect(cellText(undefined)).toBeNull();
    expect(cellText(12)).toBe("12");
    expect(cellText(true)).toBe("true");
    expect(cellText({ a: 1 })).toBe('{"a": 1}');
    expect(cellText([1, { b: "x" }, null])).toBe('[1, {"b": "x"}, null]');
  });

  it("cellText renders timestamps in Postgres's text form, and leaves text alone", () => {
    expect(cellText("2025-01-15T08:30:00.120000+00:00", "timestamp")).toBe("2025-01-15 08:30:00.12+00");
    expect(cellText("2025-01-15T10:30:00", "timestamp")).toBe("2025-01-15 10:30:00");
    expect(cellText("2025-01-15T10:30:00+05:30", "timestamp")).toBe("2025-01-15 10:30:00+05:30");
    expect(cellText("2025-01-15", "date")).toBe("2025-01-15");
    expect(cellText("2025-01-15T10:30:00", "text")).toBe("2025-01-15T10:30:00");
  });

  it("detects duplicate column names", () => {
    expect(hasDuplicateColumns(["a", "b", "a"])).toBe(true);
    expect(hasDuplicateColumns(["a", "b"])).toBe(false);
  });

  it("keeps a truncated result with duplicate columns in the browser", () => {
    const truncated = queryResponse({ truncated: true });

    expect(isServerSide({ ...truncated, columns: ["a", "b"] }, EMPTY_VIEW)).toBe(true);
    expect(isServerSide({ ...truncated, columns: ["a", "a"] }, EMPTY_VIEW)).toBe(false);
  });

  it("sends a complete result to the server only for a view the browser cannot reproduce", () => {
    const complete = queryResponse({ columns: ["a", "b"], column_types: ["text", "other"] });
    const onText = { filters: [{ index: 0, operator: "contains" as const, value: "x" }] };
    const onOther = { filters: [{ index: 1, operator: "=" as const, value: "1 day" }] };

    expect(isServerSide(complete, EMPTY_VIEW)).toBe(false);
    expect(isServerSide(complete, onText)).toBe(false);
    expect(isServerSide(complete, onOther)).toBe(true);
    expect(isServerSide({ ...complete, columns: ["a", "a"] }, onOther)).toBe(false);
  });

  it("maps a view to the server's names and drops values of valueless operators", () => {
    const options = toServerOptions(
      { sort: { index: 1, direction: "desc" }, filters: [{ index: 0, operator: "is_null", value: "x" }] },
      ["a", "b"],
    );

    expect(options).toEqual({
      sort: [{ column: "b", direction: "desc" }],
      filters: [{ column: "a", operator: "is_null", value: null }],
    });
  });
});

describe("needsServer with a cut cell", () => {
  const MARKER = " …[cut at 10000 chars]";
  const rows = [
    ["a", "x".repeat(10_000) + MARKER, "ok"],
    ["b", "short", "also ok"],
  ];
  const types: ("text" | "number")[] = ["text", "text", "text"];
  const sortBy = (index: number) => ({ sort: { index, direction: "asc" as const }, filters: [] });
  const filterBy = (index: number) => ({ filters: [{ index, operator: "contains" as const, value: "x" }] });

  it("sends a sort and a filter on a column holding a cut cell to the server", () => {
    expect(needsServer(sortBy(1), types, rows)).toBe(true);
    expect(needsServer(filterBy(1), types, rows)).toBe(true);
  });

  it("keeps a column without a cut cell in the browser, even beside one with a cut cell", () => {
    expect(needsServer(sortBy(2), types, rows)).toBe(false);
    expect(needsServer(filterBy(0), types, rows)).toBe(false);
  });

  it("does not take a value that only resembles the marker for a cut cell", () => {
    const similar = [["a", "cut at 10000 chars]"], ["b", "x …[cut at 10 chars]"], ["c", null]];

    expect(needsServer(sortBy(1), types, similar)).toBe(false);
  });
});
