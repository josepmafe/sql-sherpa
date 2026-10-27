import { describe, expect, it } from "vitest";

import { edgeAppearance, PRIMARY_EDGE_COLOR, relatedNodes } from "../../src/screens/lineage/highlight";

const DIAMOND = [
  { source: "a", target: "b" },
  { source: "a", target: "c" },
  { source: "b", target: "d" },
  { source: "c", target: "d" },
  { source: "d", target: "e" },
  { source: "x", target: "y" },
];

describe("relatedNodes", () => {
  it("returns ancestors and descendants through a diamond", () => {
    const related = relatedNodes(DIAMOND, "b");

    expect([...related.nodes].sort()).toEqual(["a", "b", "d", "e"]);
    expect([...related.edges].sort()).toEqual(["a->b", "b->d", "d->e"]);
  });

  it("a node without edges is related only to itself", () => {
    const related = relatedNodes(DIAMOND, "lonely");

    expect([...related.nodes]).toEqual(["lonely"]);
    expect(related.edges.size).toBe(0);
  });

  it("does not cross edges it was not given", () => {
    const shown = DIAMOND.filter((edge) => edge.target !== "e");

    const related = relatedNodes(shown, "a");

    expect(related.nodes.has("e")).toBe(false);
    expect([...related.nodes].sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("survives a cycle", () => {
    const related = relatedNodes(
      [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
      ],
      "a",
    );

    expect([...related.nodes].sort()).toEqual(["a", "b"]);
  });
});

describe("relatedNodes edges", () => {
  it("highlights a skip edge between two related nodes", () => {
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "a", target: "c" },
    ];

    const related = relatedNodes(edges, "b");

    expect([...related.edges].sort()).toEqual(["a->b", "a->c", "b->c"]);
  });
});

describe("edgeAppearance", () => {
  const related = relatedNodes(DIAMOND, "b");

  it("with no selection every edge has the default look", () => {
    const edge = edgeAppearance(undefined, "a->b");

    expect(edge).toEqual(edgeAppearance(undefined, "x->y"));
    expect(edge.opacity).toBe(1);
  });

  it("a selected node's chain is drawn in the primary colour and the rest fades", () => {
    const chain = edgeAppearance(related, "a->b");
    const rest = edgeAppearance(related, "x->y");

    expect(chain.stroke).toBe(PRIMARY_EDGE_COLOR);
    expect(chain.stroke).not.toBe(edgeAppearance(undefined, "a->b").stroke);
    expect(chain.opacity).toBe(1);
    expect(rest.stroke).toBe(edgeAppearance(undefined, "x->y").stroke);
    expect(rest.opacity).toBeLessThan(0.3);
  });
});
