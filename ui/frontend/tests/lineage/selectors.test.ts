import { describe, expect, it } from "vitest";

import { domainSelector, pathSelector, readMode } from "../../src/screens/lineage/selectors";

describe("pathSelector", () => {
  it.each([
    ["all", "all", "+fct_orders+"],
    [1, "all", "1+fct_orders+"],
    ["all", 5, "+fct_orders+5"],
    [3, 2, "3+fct_orders+2"],
  ] as const)("writes the depth operators (up %s, down %s)", (up, down, expected) => {
    expect(pathSelector("fct_orders", up, down)).toBe(expected);
  });
});

describe("domainSelector", () => {
  it("selects the domain's marts and their ancestors", () => {
    expect(domainSelector("sales")).toBe("+config.meta.domain:sales");
  });
});

describe("readMode", () => {
  it("restores the mode from a selector", () => {
    expect(readMode("")).toEqual({ mode: "project" });
    expect(readMode("  ")).toEqual({ mode: "project" });
    expect(readMode("+fct_orders+2")).toEqual({ mode: "path", name: "fct_orders", up: "all", down: 2 });
    expect(readMode("3+fct_orders+")).toEqual({ mode: "path", name: "fct_orders", up: 3, down: "all" });
    expect(readMode("+config.meta.domain:sales")).toEqual({ mode: "domain", domain: "sales" });
  });

  it.each(["+fct_orders", "fct_orders+", "fct_orders", "9+fct_orders+", "0+fct_orders+", "source:raw.orders"])(
    "an atom outside the three modes is custom (%s)",
    (selector) => {
      expect(readMode(selector)).toEqual({ mode: "custom" });
    },
  );

  it("a union is custom", () => {
    expect(readMode("+fct_orders+ +dim_products+")).toEqual({ mode: "custom" });
    expect(readMode("+config.meta.domain:sales +config.meta.domain:product")).toEqual({ mode: "custom" });
  });
});
