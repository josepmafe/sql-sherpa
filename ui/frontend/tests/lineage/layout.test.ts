import { describe, expect, it } from "vitest";

import { type BandNode, NODE_HEIGHT, NODE_WIDTH, orderNodes, stackBands } from "../../src/screens/lineage/layout";

const node = (id: string, layer: BandNode["layer"], sourceName?: string): BandNode => ({ id, layer, sourceName });
const ranks = (...ids: string[]) => new Map(ids.map((id, index) => [id, index]));
const NO_HEIGHTS = new Map<string, number>();

describe("stackBands", () => {
  it("places bands left to right in the spec's order", () => {
    const nodes = [
      node("exp", "exposure"),
      node("mart", "marts"),
      node("int", "intermediate"),
      node("snap", "snapshot"),
      node("stg", "staging"),
      node("seed", "seed"),
      node("src", "source", "erp"),
    ];

    const positions = stackBands(nodes, [], ranks(...nodes.map((n) => n.id)), NO_HEIGHTS);

    const xs = ["src", "seed", "stg", "snap", "int", "mart", "exp"].map((id) => positions.get(id)!.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(new Set(xs).size).toBe(7);
  });

  it("puts other beside the intermediate models", () => {
    const nodes = [node("int", "intermediate"), node("misc", "other")];

    const positions = stackBands(nodes, [], ranks("int", "misc"), NO_HEIGHTS);

    expect(positions.get("misc")!.x).toBe(positions.get("int")!.x);
  });

  it("skips a band the graph does not have", () => {
    const without = stackBands([node("stg", "staging"), node("mart", "marts")], [], ranks("stg", "mart"), NO_HEIGHTS);
    const withSnapshot = stackBands(
      [node("stg", "staging"), node("snap", "snapshot"), node("mart", "marts")],
      [],
      ranks("stg", "snap", "mart"),
      NO_HEIGHTS,
    );

    const gap = without.get("mart")!.x - without.get("stg")!.x - NODE_WIDTH;
    const gapWithSnapshot = withSnapshot.get("mart")!.x - withSnapshot.get("stg")!.x - NODE_WIDTH;
    expect(gap).toBeGreaterThan(0);
    expect(gapWithSnapshot).toBeGreaterThan(gap + NODE_WIDTH);
  });

  it("a mart that reads a mart sits one sub-column to the right", () => {
    const nodes = [node("fct_orders", "marts"), node("dim_customers", "marts")];
    const edges = [{ source: "fct_orders", target: "dim_customers" }];

    const positions = stackBands(nodes, edges, ranks("fct_orders", "dim_customers"), NO_HEIGHTS);

    expect(positions.get("dim_customers")!.x).toBeGreaterThanOrEqual(positions.get("fct_orders")!.x + NODE_WIDTH);
  });

  it("an intermediate model that reads another sits one sub-column to the right", () => {
    const nodes = [node("a", "intermediate"), node("b", "intermediate"), node("c", "intermediate")];
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "a", target: "c" },
    ];

    const positions = stackBands(nodes, edges, ranks("a", "b", "c"), NO_HEIGHTS);

    const x = (id: string) => positions.get(id)!.x;
    expect(x("b")).toBeGreaterThan(x("a"));
    // The longest chain decides, not the nearest parent: c is two columns right of a.
    expect(x("c") - x("b")).toBe(x("b") - x("a"));
  });

  it("a band without reads inside it stays one column", () => {
    const nodes = [node("s1", "staging"), node("s2", "staging"), node("m1", "marts")];

    const positions = stackBands(nodes, [{ source: "s1", target: "m1" }], ranks("s1", "s2", "m1"), NO_HEIGHTS);

    expect(positions.get("s1")!.x).toBe(positions.get("s2")!.x);
  });

  it("every edge between two bands runs left to right", () => {
    const nodes = [
      node("src", "source", "erp"),
      node("stg", "staging"),
      node("int", "intermediate"),
      node("int2", "intermediate"),
      node("mart", "marts"),
      node("mart2", "marts"),
      node("exp", "exposure"),
    ];
    const edges = [
      { source: "src", target: "stg" },
      { source: "stg", target: "int" },
      { source: "int", target: "int2" },
      { source: "int2", target: "mart" },
      { source: "mart", target: "mart2" },
      { source: "mart2", target: "exp" },
    ];

    const positions = stackBands(nodes, edges, ranks(...nodes.map((n) => n.id)), NO_HEIGHTS);

    for (const { source, target } of edges) {
      expect(positions.get(source)!.x + NODE_WIDTH).toBeLessThanOrEqual(positions.get(target)!.x);
    }
  });

  it("stacks nodes of different heights without overlap", () => {
    const nodes = [node("a", "marts"), node("b", "marts"), node("c", "marts")];

    const positions = stackBands(nodes, [], ranks("c", "a", "b"), new Map([["a", 300]]));

    const y = (id: string) => positions.get(id)!.y;
    expect(y("c")).toBeLessThan(y("a"));
    expect(y("c") + NODE_HEIGHT).toBeLessThanOrEqual(y("a"));
    expect(y("a") + 300).toBeLessThanOrEqual(y("b")); // b and c fall back to NODE_HEIGHT
  });

  it("a node without edges stays in its band", () => {
    const nodes = [node("src", "source", "erp"), node("mart", "marts"), node("dim_date", "marts")];

    const positions = stackBands(nodes, [], ranks("src", "mart", "dim_date"), NO_HEIGHTS);

    expect(positions.get("dim_date")!.x).toBe(positions.get("mart")!.x);
    expect(positions.get("src")!.x).toBeLessThan(positions.get("mart")!.x);
  });
});

describe("orderNodes", () => {
  it("keeps a source system's tables together", async () => {
    // The edges would interleave the two systems: erp.a and crm.b feed one staging model, erp.c and crm.d the other.
    const nodes = [
      node("erp.a", "source", "erp"),
      node("crm.b", "source", "crm"),
      node("erp.c", "source", "erp"),
      node("crm.d", "source", "crm"),
      node("stg1", "staging"),
      node("stg2", "staging"),
    ];
    const edges = [
      { source: "erp.a", target: "stg1" },
      { source: "crm.b", target: "stg1" },
      { source: "erp.c", target: "stg2" },
      { source: "crm.d", target: "stg2" },
    ];

    const order = await orderNodes(nodes, edges);

    const sources = ["erp.a", "crm.b", "erp.c", "crm.d"].sort((a, b) => order.get(a)! - order.get(b)!);
    expect(sources.map((id) => id.split(".")[0])).toEqual(["crm", "crm", "erp", "erp"]);
    expect(new Set(sources.map((id) => order.get(id))).size).toBe(4);
  }, 20_000);

  it("ranks an empty graph and a single node", async () => {
    expect((await orderNodes([], [])).size).toBe(0);
    expect([...(await orderNodes([node("only", "marts")], [])).entries()]).toEqual([["only", 0]]);
  });
});
