import { describe, expect, it } from "vitest";

import { COLUMN_ROW_HEIGHT, NODE_HEIGHT } from "../../src/screens/lineage/layout";
import { isInView, rowBox } from "../../src/screens/lineage/reveal";

const SIZE = { width: 800, height: 600 };

describe("isInView", () => {
  it("a node fully inside the canvas is in view", () => {
    expect(isInView({ x: 100, y: 100 }, { x: 0, y: 0, zoom: 1 }, SIZE)).toBe(true);
  });

  it("a node cut by the right edge of a narrowed canvas is not", () => {
    expect(isInView({ x: 700, y: 100 }, { x: 0, y: 0, zoom: 1 }, SIZE)).toBe(false);
  });

  it("a node exactly filling the corner is in view, one pixel more is not", () => {
    expect(isInView({ x: 580, y: 516 }, { x: 0, y: 0, zoom: 1 }, SIZE)).toBe(true);
    expect(isInView({ x: 581, y: 516 }, { x: 0, y: 0, zoom: 1 }, SIZE)).toBe(false);
  });

  it("the pan and the zoom move and scale the node", () => {
    expect(isInView({ x: 100, y: 100 }, { x: -150, y: 0, zoom: 1 }, SIZE)).toBe(false);
    expect(isInView({ x: 100, y: 100 }, { x: 0, y: 0, zoom: 4 }, SIZE)).toBe(false);
    expect(isInView({ x: 1000, y: 1000 }, { x: 0, y: 0, zoom: 0.2 }, SIZE)).toBe(true);
  });

  it("a row far down a tall node is judged by its own box, not by the node's header", () => {
    const row = { x: 100, y: 560 };

    expect(isInView(row, { x: 0, y: 0, zoom: 1 }, SIZE)).toBe(false); // 560 + 84 > 600
    expect(isInView(row, { x: 0, y: 0, zoom: 1 }, SIZE, COLUMN_ROW_HEIGHT)).toBe(true);
    expect(isInView({ x: 100, y: 580 }, { x: 0, y: 0, zoom: 1 }, SIZE, COLUMN_ROW_HEIGHT)).toBe(false);
  });
});

describe("rowBox", () => {
  it("is the node's header without a row, and the row's own slot under it with one", () => {
    expect(rowBox({ x: 10, y: 20 }, undefined)).toEqual({ position: { x: 10, y: 20 }, height: NODE_HEIGHT });
    expect(rowBox({ x: 10, y: 20 }, 2)).toEqual({
      position: { x: 10, y: 20 + NODE_HEIGHT + 2 * COLUMN_ROW_HEIGHT },
      height: COLUMN_ROW_HEIGHT,
    });
  });
});
