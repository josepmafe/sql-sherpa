import { describe, expect, it } from "vitest";

import { type LayoutNode, layoutGraph } from "../../src/screens/lineage/layout";

describe("layoutGraph", () => {
  it("puts each layer in its own column, left to right", async () => {
    const nodes: LayoutNode[] = [
      { id: "src", layer: "source" },
      { id: "stg", layer: "staging" },
      { id: "int", layer: "intermediate" },
      { id: "mart", layer: "marts" },
      { id: "mart2", layer: "marts" },
      { id: "seed", layer: "seed" },
      { id: "snap", layer: "snapshot" },
    ];
    const edges = [
      { source: "src", target: "stg" },
      { source: "stg", target: "int" },
      { source: "int", target: "mart" },
      { source: "stg", target: "mart2" },
      { source: "seed", target: "snap" },
    ];

    const positions = await layoutGraph(nodes, edges);

    const x = (id: string) => positions.get(id)!.x;
    expect(x("src")).toBe(x("seed"));
    expect(x("stg")).toBe(x("snap"));
    expect(x("src")).toBeLessThan(x("stg"));
    expect(x("stg")).toBeLessThan(x("int"));
    expect(x("int")).toBeLessThan(x("mart"));
    expect(x("mart")).toBe(x("mart2"));
  });

  it("lays out a graph with a single node", async () => {
    const positions = await layoutGraph([{ id: "only", layer: "marts" }], []);

    expect([...positions.keys()]).toEqual(["only"]);
  });

  it("lays out an empty graph", async () => {
    const positions = await layoutGraph([], []);

    expect(positions.size).toBe(0);
  });
});
