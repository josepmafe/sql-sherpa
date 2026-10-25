import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HISTORY_LIMIT,
  STORAGE_KEY,
  appendHistory,
  closeWorksheet,
  freshState,
  insertSelect,
  loadExplorerState,
  saveExplorerState,
  updateWorksheet,
  type HistoryEntry,
} from "../src/screens/explorer/worksheetStore";

const ENTRY: HistoryEntry = {
  sql: "select 1",
  ranAt: "2026-10-25T10:00:00.000Z",
  returnedRows: 1,
  status: "done",
};

describe("worksheetStore", () => {
  let warn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    localStorage.clear();
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => warn.mockRestore());

  it("recovers from corrupted storage", () => {
    const corrupted = [
      "{not json",
      JSON.stringify({ worksheets: [], activeId: "x", history: [] }),
      JSON.stringify({
        worksheets: [{ id: "a", name: "A", sql: "" }],
        activeId: "missing",
        history: [],
      }),
      JSON.stringify({
        worksheets: [{ id: "a", name: "A", sql: 1 }],
        activeId: "a",
        history: [],
      }),
      JSON.stringify({
        worksheets: [{ id: "a", name: "A", sql: "" }],
        activeId: "a",
        history: [{ ...ENTRY, status: "exploded" }],
      }),
      JSON.stringify([]),
    ];

    for (const raw of corrupted) {
      warn.mockClear();
      localStorage.setItem(STORAGE_KEY, raw);

      const state = loadExplorerState();

      expect(state.worksheets).toHaveLength(1);
      expect(state.worksheets[0]!).toMatchObject({
        name: "Worksheet 1",
        sql: "",
      });
      expect(state.activeId).toBe(state.worksheets[0]!.id);
      expect(state.history).toEqual([]);
      expect(warn).toHaveBeenCalledOnce();
    }
  });

  it("round-trips a valid state without warning", () => {
    const state = appendHistory(freshState(), ENTRY);
    saveExplorerState(state);

    expect(loadExplorerState()).toEqual(state);
    expect(warn).not.toHaveBeenCalled();
  });

  it("starts fresh without warning when nothing is stored", () => {
    expect(loadExplorerState().worksheets).toHaveLength(1);
    expect(warn).not.toHaveBeenCalled();
  });

  it("history keeps the last 200", () => {
    let state = freshState();
    for (let n = 0; n < HISTORY_LIMIT + 5; n++) state = appendHistory(state, { ...ENTRY, sql: `select ${n}` });

    expect(HISTORY_LIMIT).toBe(200);
    expect(state.history).toHaveLength(200);
    expect(state.history[0]!.sql).toBe("select 5");
    expect(state.history[199]!.sql).toBe("select 204");
  });

  it("keeps the last worksheet and moves the active tab when the active one closes", () => {
    const only = freshState();
    expect(closeWorksheet(only, only.activeId)).toBe(only);

    const two = {
      ...only,
      worksheets: [...only.worksheets, { id: "b", name: "Worksheet 2", sql: "" }],
    };
    const closed = closeWorksheet(two, only.activeId);
    expect(closed.worksheets.map((w) => w.id)).toEqual(["b"]);
    expect(closed.activeId).toBe("b");
  });

  it("insert SELECT fills an empty worksheet and appends to a used one", () => {
    const empty = freshState();
    const filled = insertSelect(empty, "marts.fct_orders");
    expect(filled.worksheets[0]!.sql).toBe("select *\nfrom marts.fct_orders\nlimit 100");

    const used = updateWorksheet(empty, empty.activeId, { sql: "select 1;\n" });
    expect(insertSelect(used, "marts.dim_products").worksheets[0]!.sql).toBe(
      "select 1;\n\nselect *\nfrom marts.dim_products\nlimit 100",
    );
  });
});
