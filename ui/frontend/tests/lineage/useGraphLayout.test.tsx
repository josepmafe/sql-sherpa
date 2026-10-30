import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NODE_HEIGHT, orderNodes } from "../../src/screens/lineage/layout";
import { useGraphLayout } from "../../src/screens/lineage/useGraphLayout";

vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

const order = vi.mocked(orderNodes);
const marts = (...ids: string[]) => ids.map((id) => ({ id, layer: "marts" as const, sourceName: undefined }));
const FIRST = marts("a");
const SECOND = marts("b");
const NO_EDGES: { source: string; target: string }[] = [];
const NO_HEIGHTS = new Map<string, number>();
const ROW_GAP = 24;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("useGraphLayout", () => {
  beforeEach(() => {
    order.mockReset();
  });

  it("ignores a superseded order", async () => {
    const slow = deferred<Map<string, number>>();
    order.mockReturnValueOnce(slow.promise).mockResolvedValueOnce(new Map([["b", 0]]));
    const { result, rerender } = renderHook(({ nodes }) => useGraphLayout(nodes, NO_EDGES, NO_HEIGHTS), {
      initialProps: { nodes: FIRST },
    });
    rerender({ nodes: SECOND });
    await waitFor(() => expect(result.current.state).toBe("ready"));

    await act(async () => slow.resolve(new Map([["a", 0]])));

    expect([...result.current.positions!.keys()]).toEqual(["b"]);
    expect(result.current.state).toBe("ready");
  });

  it("keeps the previous positions when an order fails", async () => {
    order.mockResolvedValueOnce(new Map([["a", 0]])).mockRejectedValueOnce(new Error("elk"));
    const { result, rerender } = renderHook(({ nodes }) => useGraphLayout(nodes, NO_EDGES, NO_HEIGHTS), {
      initialProps: { nodes: FIRST },
    });
    await waitFor(() => expect(result.current.state).toBe("ready"));

    rerender({ nodes: SECOND });

    await waitFor(() => expect(result.current.state).toBe("error"));
    expect([...result.current.positions!.keys()]).toEqual(["a"]);
  });

  it("restacks on expansion without re-running ELK and ignores a superseded order", async () => {
    const two = marts("a", "b");
    const three = marts("a", "b", "c");
    const slow = deferred<Map<string, number>>();
    order.mockResolvedValueOnce(new Map([["a", 0], ["b", 1]])).mockReturnValueOnce(slow.promise);
    const { result, rerender } = renderHook(({ nodes, heights }) => useGraphLayout(nodes, NO_EDGES, heights), {
      initialProps: { nodes: two, heights: NO_HEIGHTS },
    });
    await waitFor(() => expect(result.current.state).toBe("ready"));
    expect(result.current.positions!.get("b")!.y).toBe(NODE_HEIGHT + ROW_GAP);

    // An expansion: new heights, same topology. Restacks at once, no second ELK run.
    rerender({ nodes: two, heights: new Map([["a", 400]]) });

    expect(order).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe("ready");
    expect(result.current.positions!.get("b")!.y).toBe(400 + ROW_GAP);

    // A topology change starts a run (the slow one) that a newer topology supersedes.
    rerender({ nodes: three, heights: new Map([["a", 400]]) });
    expect(order).toHaveBeenCalledTimes(2);
    order.mockResolvedValueOnce(new Map([["c", 0], ["a", 1], ["b", 2]]));
    rerender({ nodes: [...three], heights: new Map([["a", 400], ["c", 200]]) });
    await waitFor(() => expect(order).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.positions!.get("c")!.y).toBe(0));
    const newer = result.current.positions!;

    await act(async () => slow.resolve(new Map([["a", 2], ["b", 1], ["c", 0]])));

    expect(result.current.positions).toBe(newer);
    expect(newer.get("a")!.y).toBe(200 + ROW_GAP);
    expect(newer.get("b")!.y).toBe(200 + ROW_GAP + 400 + ROW_GAP);
    expect(order).toHaveBeenCalledTimes(3);
  });
});
