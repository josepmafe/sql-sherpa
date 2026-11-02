import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readLayout, writeLayout } from "../../src/components/panes/layoutStorage";

const LIMITS = {
  tree: { min: 200, max: 480 },
  inspector: { min: 240, max: 600 },
};

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("readLayout", () => {
  it("returns undefined when nothing is stored", () => {
    expect(readLayout("explorer", LIMITS)).toBeUndefined();
  });

  it("returns the stored sizes and flags", () => {
    localStorage.setItem(
      "sherpa.v1.layout.explorer",
      JSON.stringify({
        sizes: { tree: 300, inspector: 400 },
        collapsed: { inspector: true },
      }),
    );

    expect(readLayout("explorer", LIMITS)).toEqual({
      sizes: { tree: 300, inspector: 400 },
      collapsed: { inspector: true },
    });
  });

  it("returns undefined and warns with the key when the value is not JSON", () => {
    localStorage.setItem("sherpa.v1.layout.explorer", '{"x":');
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(readLayout("explorer", LIMITS)).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("sherpa.v1.layout.explorer"));
  });

  it.each([
    ["an array", "[1,2]"],
    ["a size that is a string", '{"sizes":{"tree":"wide"},"collapsed":{}}'],
    ["a flag that is a number", '{"sizes":{},"collapsed":{"tree":1}}'],
    ["missing collapsed", '{"sizes":{}}'],
  ])("returns undefined for %s", (_name, raw) => {
    localStorage.setItem("sherpa.v1.layout.explorer", raw);
    vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(readLayout("explorer", LIMITS)).toBeUndefined();
  });

  it("returns undefined when reading storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(readLayout("explorer", LIMITS)).toBeUndefined();
  });

  it("clamps a stored size to the pane's limits", () => {
    localStorage.setItem(
      "sherpa.v1.layout.explorer",
      JSON.stringify({ sizes: { tree: 5000, inspector: 10 }, collapsed: {} }),
    );

    expect(readLayout("explorer", LIMITS)?.sizes).toEqual({
      tree: 480,
      inspector: 240,
    });
  });

  it("drops a pane that has no limits", () => {
    localStorage.setItem(
      "sherpa.v1.layout.explorer",
      JSON.stringify({
        sizes: { tree: 300, gone: 300 },
        collapsed: { gone: true },
      }),
    );

    expect(readLayout("explorer", LIMITS)).toEqual({
      sizes: { tree: 300 },
      collapsed: {},
    });
  });
});

describe("writeLayout", () => {
  it("writes the value under its own key and nothing else", () => {
    writeLayout("explorer", {
      sizes: { tree: 300 },
      collapsed: { inspector: true },
    });

    expect(Object.keys(localStorage)).toEqual(["sherpa.v1.layout.explorer"]);
    expect(JSON.parse(localStorage.getItem("sherpa.v1.layout.explorer") ?? "")).toEqual({
      sizes: { tree: 300 },
      collapsed: { inspector: true },
    });
  });

  it("carries on when storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(() => writeLayout("explorer", { sizes: {}, collapsed: {} })).not.toThrow();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("sherpa.v1.layout.explorer"), expect.anything());
  });
});
