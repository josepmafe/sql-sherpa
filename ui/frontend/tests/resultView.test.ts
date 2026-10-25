import { describe, expect, it } from "vitest";

import { applyView, cellText, hasDuplicateColumns, isServerSide, toServerOptions } from "../src/components/resultView";

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

  it("sorts unparseable values after valid ones instead of leaving the order undefined", () => {
    const rows = [
      ["bad", "garbage"],
      ["ok", "2025-01-15"],
    ];

    const sorted = applyView(rows, ["text", "date"], { sort: { index: 1, direction: "asc" }, filters: [] });

    expect(ids(sorted)).toEqual(["ok", "bad"]);
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
  it("cellText renders NULL as null and objects as JSON", () => {
    expect(cellText(null)).toBeNull();
    expect(cellText(undefined)).toBeNull();
    expect(cellText(12)).toBe("12");
    expect(cellText({ a: 1 })).toBe('{"a":1}');
  });

  it("detects duplicate column names", () => {
    expect(hasDuplicateColumns(["a", "b", "a"])).toBe(true);
    expect(hasDuplicateColumns(["a", "b"])).toBe(false);
  });

  it("keeps a truncated result with duplicate columns in the browser", () => {
    const base = { mode: "client", truncated: true };

    expect(isServerSide({ ...base, columns: ["a", "b"] } as never)).toBe(true);
    expect(isServerSide({ ...base, columns: ["a", "a"] } as never)).toBe(false);
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
