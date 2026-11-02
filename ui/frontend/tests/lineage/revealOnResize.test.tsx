import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NODE_HEIGHT, NODE_WIDTH } from "../../src/screens/lineage/layout";
import { useKeepSelectedInView } from "../../src/screens/lineage/reveal";

const WIDTH = 800;
const setCenter = vi.fn();
const state = { height: 500, position: { x: 100, y: 100 }, zoom: 1 };
vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({
    getNode: () => ({ position: state.position }),
    getViewport: () => ({ x: 0, y: 0, zoom: state.zoom }),
    setCenter,
  }),
  useStoreApi: () => ({ getState: () => ({ width: WIDTH, height: state.height }) }),
  useStore: (select: (s: { height: number }) => number) => select({ height: state.height }),
}));

type Props = { id: string | undefined; ok: boolean };

describe("useKeepSelectedInView", () => {
  beforeEach(() => {
    setCenter.mockReset();
    Object.assign(state, { height: 500, position: { x: 100, y: 100 }, zoom: 1 });
  });

  const mount = (selected: string | undefined = "model.s.a", ready = true) =>
    renderHook<void, Props>(({ id, ok }) => useKeepSelectedInView(id, ok), {
      initialProps: { id: selected, ok: ready },
    });

  it("a canvas that shrinks under the selected node centres it at the current zoom", () => {
    state.position = { x: 100, y: 400 };
    state.zoom = 0.8;
    const view = mount();

    state.height = 300;
    view.rerender({ id: "model.s.a", ok: true });

    expect(setCenter).toHaveBeenCalledWith(100 + NODE_WIDTH / 2, 400 + NODE_HEIGHT / 2, { zoom: 0.8, duration: 0 });
  });

  it("a canvas that shrinks but still holds the selected node leaves the viewport alone", () => {
    const view = mount();

    state.height = 300;
    view.rerender({ id: "model.s.a", ok: true });

    expect(setCenter).not.toHaveBeenCalled();
  });

  it.each([
    ["nothing is selected", undefined, true],
    ["the graph has not been fitted yet", "model.s.a", false],
  ] as const)("a resize while %s moves nothing", (_case, id, ok) => {
    state.position = { x: 100, y: 400 };
    const selected: string | undefined = id;
    const view = mount(selected, ok);

    state.height = 300;
    view.rerender({ id: selected, ok });

    expect(setCenter).not.toHaveBeenCalled();
  });

  it("the first render moves nothing even when the node is out of view", () => {
    state.position = { x: 100, y: 900 };

    mount();

    expect(setCenter).not.toHaveBeenCalled();
  });
});
