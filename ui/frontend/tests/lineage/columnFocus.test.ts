import { describe, expect, it } from "vitest";

import { focusView, nodeRows } from "../../src/screens/lineage/columnFocus";
import { columnFocusView, columnView, focusColumn } from "../fixtures/api";

const FCT = "model.s.fct_orders";
const STG = "model.s.stg_orders";
const RAW = "source.s.shop.orders";

// fct_orders.net_revenue <- stg_orders.amount <- shop.orders.amount, and fct_orders.net_revenue -> dim.revenue
const FOCUS = columnFocusView({
  focus: { node: FCT, column: "net_revenue" },
  columns: [
    focusColumn({ node: FCT, name: "net_revenue", direction: "focus" }),
    focusColumn({ node: STG, name: "amount", direction: "upstream" }),
    focusColumn({ node: RAW, name: "amount", direction: "upstream" }),
    focusColumn({ node: "model.s.dim", name: "revenue", direction: "downstream" }),
  ],
  edges: [
    { parent: { node: STG, column: "amount" }, child: { node: FCT, column: "net_revenue" } },
    { parent: { node: RAW, column: "amount" }, child: { node: STG, column: "amount" } },
    { parent: { node: FCT, column: "net_revenue" }, child: { node: "model.s.dim", column: "revenue" } },
  ],
});

describe("focusView", () => {
  it("expands each shown node with its related columns", () => {
    const view = focusView(FOCUS, new Set([FCT, STG, RAW, "model.s.dim"]));

    expect(view.expanded).toEqual(
      new Map([
        [FCT, new Set(["net_revenue"])],
        [STG, new Set(["amount"])],
        [RAW, new Set(["amount"])],
        ["model.s.dim", new Set(["revenue"])],
      ]),
    );
    expect(view.outside).toBe(0);
  });

  it("draws only edges between shown nodes", () => {
    const view = focusView(FOCUS, new Set([FCT, STG]));

    expect(view.edges).toEqual([FOCUS.edges[0]]);
  });

  it("column focus counts related columns outside the view", () => {
    const view = focusView(FOCUS, new Set([FCT, STG]));

    expect(view.outside).toBe(2);
    expect([...view.expanded.keys()]).toEqual([FCT, STG]);
  });

  it("a focus with no related column outside and none shown is empty", () => {
    const view = focusView(columnFocusView({ focus: { node: FCT, column: "k" } }), new Set([FCT]));

    expect(view).toEqual({ expanded: new Map(), edges: [], outside: 0 });
  });
});

describe("nodeRows", () => {
  const COLUMNS = [
    columnView({ name: "net_revenue" }),
    columnView({ name: "units" }),
    columnView({ name: "order_id" }),
  ];
  const names = (rows: { column: { name: string } }[]) => rows.map((r) => r.column.name);

  it("a collapsed node has no rows", () => {
    expect(nodeRows(COLUMNS, { expanded: false, related: undefined, focused: undefined })).toEqual({ rows: [], more: 0 });
  });

  it("an expanded node lists every column, none marked without a focus", () => {
    const { rows, more } = nodeRows(COLUMNS, { expanded: true, related: undefined, focused: undefined });

    expect(names(rows)).toEqual(["net_revenue", "units", "order_id"]);
    expect(rows.map((r) => r.role)).toEqual([undefined, undefined, undefined]);
    expect(more).toBe(0);
  });

  it("a related node shows its related columns and counts the rest", () => {
    const { rows, more } = nodeRows(COLUMNS, { expanded: false, related: new Set(["order_id"]), focused: undefined });

    expect(names(rows)).toEqual(["order_id"]);
    expect(rows[0]?.role).toBe("related");
    expect(more).toBe(2);
  });

  it("the focused column is marked, and so is each related one when the rest is shown too, still counted", () => {
    const { rows, more } = nodeRows(COLUMNS, {
      expanded: true,
      related: new Set(["net_revenue", "order_id"]),
      focused: "net_revenue",
    });

    expect(rows.map((r) => r.role)).toEqual(["focus", "other", "related"]);
    expect(more).toBe(1);
  });

  it("a related column the node's columns lack is not invented", () => {
    const { rows, more } = nodeRows(COLUMNS, { expanded: false, related: new Set(["gone"]), focused: undefined });

    expect(rows).toEqual([]);
    expect(more).toBe(3);
  });
});
