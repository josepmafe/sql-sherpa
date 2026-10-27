import { act, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { type LineageParams, parseLineageSearch, toSearchParams, useLineageParams } from "../../src/screens/lineage/url";

describe("lineage URL state", () => {
  it("round-trips every parameter", () => {
    const params: LineageParams = {
      select: "+fct_orders+2",
      layers: ["marts", "staging"],
      failingOnly: true,
      node: "model.sherpa.fct_orders",
    };

    const search = toSearchParams(params);

    expect(search.toString()).toBe(
      "select=%2Bfct_orders%2B2&layers=marts%2Cstaging&failing=1&node=model.sherpa.fct_orders",
    );
    expect(parseLineageSearch(search)).toEqual(params);
  });

  it("omits the defaults", () => {
    const search = toSearchParams({ select: "", layers: undefined, failingOnly: false, node: undefined });

    expect(search.toString()).toBe("");
    expect(parseLineageSearch(new URLSearchParams())).toEqual({
      select: "",
      layers: undefined,
      failingOnly: false,
      node: undefined,
    });
  });

  it("ignores an invalid layer", () => {
    const parsed = parseLineageSearch(new URLSearchParams("layers=marts,bogus&failing=yes"));
    const onlyInvalid = parseLineageSearch(new URLSearchParams("layers=bogus"));

    expect(parsed.layers).toEqual(["marts"]);
    expect(parsed.failingOnly).toBe(false);
    expect(onlyInvalid.layers).toBeUndefined();
  });

  describe("useLineageParams", () => {
    it("selecting a node replaces the history entry", () => {
      const { router, hook } = renderProbe();

      act(() => hook.current[1]({ node: "model.sherpa.fct_orders" }));

      expect(router.state.historyAction).toBe("REPLACE");
      expect(router.state.location.search).toContain("node=model.sherpa.fct_orders");
    });

    it("changing the selector pushes a history entry", () => {
      const { router, hook } = renderProbe();

      act(() => hook.current[1]({ select: "+fct_orders+" }));

      expect(router.state.historyAction).toBe("PUSH");
      expect(hook.current[0].select).toBe("+fct_orders+");
    });
  });
});

function renderProbe() {
  const hook: { current: ReturnType<typeof useLineageParams> } = { current: undefined as never };
  function Probe() {
    hook.current = useLineageParams();
    return null;
  }
  const router = createMemoryRouter([{ path: "/lineage", element: <Probe /> }], {
    initialEntries: ["/lineage?select=fct_orders"],
  });
  render(<RouterProvider router={router} />);
  return { router, hook };
}
