import { describe, expect, it } from "vitest";

import { type LayoutNode, layoutGraph } from "../../src/screens/lineage/layout";

describe("layoutGraph", () => {
  it("puts each layer in its own column, left to right", async () => {
    // Edges inside a layer (int -> int2, mart -> mart2) may widen its column but never overlap the next one.
    const nodes: LayoutNode[] = [
      { id: "src", layer: "source" },
      { id: "seed", layer: "seed" },
      { id: "stg", layer: "staging" },
      { id: "snap", layer: "snapshot" },
      { id: "int", layer: "intermediate" },
      { id: "int2", layer: "intermediate" },
      { id: "mart", layer: "marts" },
      { id: "mart2", layer: "marts" },
      { id: "exp", layer: "exposure" },
    ];
    const edges = [
      { source: "src", target: "stg" },
      { source: "seed", target: "snap" },
      { source: "stg", target: "int" },
      { source: "int", target: "int2" },
      { source: "int2", target: "mart" },
      { source: "mart", target: "mart2" },
      { source: "mart2", target: "exp" },
    ];

    const positions = await layoutGraph(nodes, edges);

    const columns = [["src", "seed"], ["stg", "snap"], ["int", "int2"], ["mart", "mart2"], ["exp"]];
    const xs = (ids: string[]) => ids.map((id) => positions.get(id)!.x);
    columns.slice(1).forEach((ids, index) => {
      expect(Math.max(...xs(columns[index]!))).toBeLessThan(Math.min(...xs(ids)));
    });
  });

  it("keeps a node without edges in its layer's column", async () => {
    // dim_date has no edges: packed beside the connected components it used to land in the sources' column.
    const nodes: LayoutNode[] = [
      { id: "src", layer: "source" },
      { id: "stg", layer: "staging" },
      { id: "mart", layer: "marts" },
      { id: "dim_date", layer: "marts" },
    ];

    const positions = await layoutGraph(nodes, [{ source: "src", target: "stg" }]);

    const x = (id: string) => positions.get(id)!.x;
    expect(x("src")).toBeLessThan(x("stg"));
    expect(x("stg")).toBeLessThan(x("mart"));
    expect(x("mart")).toBe(x("dim_date"));
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
