import { describe, expect, it } from "vitest";

import { relatedNodes } from "../../src/screens/lineage/highlight";

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
