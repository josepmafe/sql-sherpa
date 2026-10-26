import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HISTORY_LIMIT,
  STORAGE_KEY,
  appendHistory,
  closeWorksheet,
  freshState,
  insertSelect,
  loadExplorerState,
  mergeExplorerState,
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

  it("starts fresh and saves nothing, with warnings, when the browser blocks localStorage itself", () => {
    // Blocked site data makes the `localStorage` getter throw, before any getItem.
    const getter = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });

    try {
      const state = loadExplorerState();
      saveExplorerState(state);

      expect(state.worksheets).toHaveLength(1);
      expect(state.history).toEqual([]);
      expect(warn).toHaveBeenCalledTimes(2);
    } finally {
      getter.mockRestore();
    }
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

  describe("mergeExplorerState (another tab saved)", () => {
    const A = { id: "a", name: "A", sql: "select 1" };
    const B = { id: "b", name: "B", sql: "select 2" };
    const C = { id: "c", name: "C", sql: "select 3" };
    const at = (minute: number, sql = "select 1"): HistoryEntry => ({
      ...ENTRY,
      sql,
      ranAt: `2026-10-25T10:${String(minute).padStart(2, "0")}:00.000Z`,
    });

    it("adds the other tab's new worksheets after its own, keeping its own copy and active tab", () => {
      const local = { worksheets: [A, B], activeId: "b", history: [] };
      const other = { worksheets: [{ ...B, sql: "select 'theirs'" }, C], activeId: "c", history: [] };

      const merged = mergeExplorerState(local, other);

      expect(merged.worksheets).toEqual([A, B, { ...C, runOnOpen: false }]);
      expect(merged.activeId).toBe("b");
    });

    it("never runs a worksheet that arrives from another tab on open", () => {
      const local = { worksheets: [A], activeId: "a", history: [] };
      const other = { worksheets: [A, { ...B, runOnOpen: true }], activeId: "b", history: [] };

      expect(mergeExplorerState(local, other).worksheets[1]).toEqual({ ...B, runOnOpen: false });
    });

    it("unions history by time and SQL, in time order, keeping the last 200", () => {
      const local = { worksheets: [A], activeId: "a", history: [at(1), at(3)] };
      const other = { worksheets: [A], activeId: "a", history: [at(1), at(2), at(3, "select 9")] };

      const merged = mergeExplorerState(local, other);

      expect(merged.history).toEqual([at(1), at(2), at(3), at(3, "select 9")]);
      const newer = Array.from({ length: HISTORY_LIMIT }, (_, n) => ({
        ...ENTRY,
        ranAt: `2026-10-26T10:00:00.${String(n).padStart(3, "0")}Z`,
      }));
      // The other tab's three entries are older than all 200 local ones, so they fall off.
      expect(mergeExplorerState({ ...local, history: newer }, other).history).toEqual(newer);
    });

    it("returns the same state when the other tab adds nothing, so two tabs do not save back and forth", () => {
      const local = { worksheets: [A, B], activeId: "a", history: [at(1)] };
      const other = { worksheets: [B, { ...A, sql: "changed" }], activeId: "b", history: [at(1)] };

      expect(mergeExplorerState(local, other)).toBe(local);
    });
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
