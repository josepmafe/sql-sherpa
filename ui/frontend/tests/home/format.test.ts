import { describe, expect, it } from "vitest";

import { describeChange, formatChange, formatValue } from "../../src/screens/home/format";
import { tile } from "../fixtures/api";

describe("formatValue", () => {
  it.each([
    [{ format: "currency", decimals: 0, value: "34812.4" }, "€34,812"],
    [{ format: "currency", decimals: 2, value: "62.386" }, "€62.39"],
    [{ format: "count", decimals: 0, value: "1234" }, "1,234"],
    [{ format: "count", decimals: 1, value: "7" }, "7.0"],
    [{ format: "percent", decimals: 1, value: "0.0918" }, "9.2%"],
    [{ format: "percent", decimals: 0, value: "0.5" }, "50%"],
    [{ format: "percent", decimals: 2, value: "0.01234" }, "1.23%"],
    [{ format: "currency", decimals: 0, value: "-1500.4" }, "-€1,500"],
    [{ format: "currency", decimals: 0, value: "12345678.9" }, "€12,345,679"],
    [{ format: "count", decimals: 0, value: null }, "—"],
  ] as const)("%j gives %s", (overrides, expected) => {
    expect(formatValue(tile(overrides))).toBe(expected);
  });
});

describe("formatChange", () => {
  it.each([
    [{ change: "0.12", change_kind: "relative", direction: "up" }, "▲ 12.0%"],
    [{ change: "-0.0345", change_kind: "relative", direction: "down" }, "▼ 3.5%"],
    [{ change: "-0.007", change_kind: "points", direction: "down" }, "▼ 0.7 pp"],
    [{ change: "0.02", change_kind: "points", direction: "up" }, "▲ 2.0 pp"],
    [{ change: "0", change_kind: "relative", direction: "flat" }, "0.0%"],
    [{ change: "0.0001", change_kind: "relative", direction: "flat" }, "0.0%"],
    [{ change: "0.0002", change_kind: "points", direction: "flat" }, "0.0 pp"],
    [{ change: null, change_kind: "relative", direction: null }, "—"],
  ] as const)("%j gives %s", (overrides, expected) => {
    expect(formatChange(tile(overrides))).toBe(expected);
  });
});

describe("describeChange", () => {
  it("says an improvement for a good change", () => {
    const refunds = tile({ format: "percent", change: "-0.007", change_kind: "points", direction: "down", good: true });

    expect(describeChange(refunds)).toBe("down 0.7 pp on the previous 30 days, an improvement");
  });

  it("says a deterioration for a bad change", () => {
    const revenue = tile({ change: "-0.05", direction: "down", good: false });

    expect(describeChange(revenue)).toBe("down 5.0% on the previous 30 days, a deterioration");
  });

  it("says unchanged for a flat one", () => {
    expect(describeChange(tile({ change: "0", direction: "flat", good: null }))).toBe(
      "unchanged on the previous 30 days",
    );
  });

  it("says unchanged for a flat percentage-point one too", () => {
    const refunds = tile({ format: "percent", change: "0.0002", change_kind: "points", direction: "flat", good: null });

    expect(describeChange(refunds)).toBe("unchanged on the previous 30 days");
  });

  it("says there is no comparison without a change", () => {
    expect(describeChange(tile({ change: null, direction: null, good: null }))).toBe(
      "no comparison with the previous 30 days",
    );
  });
});
