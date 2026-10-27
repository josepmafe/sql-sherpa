import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { layoutGraph } from "../../src/screens/lineage/layout";
import { useGraphLayout } from "../../src/screens/lineage/useGraphLayout";

vi.mock("../../src/screens/lineage/layout", () => ({ layoutGraph: vi.fn() }));

const layout = vi.mocked(layoutGraph);
const FIRST = [{ id: "a", layer: "marts" as const }];
const SECOND = [{ id: "b", layer: "marts" as const }];
const NO_EDGES: { source: string; target: string }[] = [];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("useGraphLayout", () => {
  beforeEach(() => {
    layout.mockReset();
  });

  it("ignores a superseded layout", async () => {
    const slow = deferred<Map<string, { x: number; y: number }>>();
    layout.mockReturnValueOnce(slow.promise).mockResolvedValueOnce(new Map([["b", { x: 2, y: 2 }]]));
    const { result, rerender } = renderHook(({ nodes }) => useGraphLayout(nodes, NO_EDGES), {
      initialProps: { nodes: FIRST },
    });
    rerender({ nodes: SECOND });
    await waitFor(() => expect(result.current.state).toBe("ready"));

    await act(async () => slow.resolve(new Map([["a", { x: 1, y: 1 }]])));

    expect([...result.current.positions!.keys()]).toEqual(["b"]);
    expect(result.current.state).toBe("ready");
  });

  it("keeps the previous positions when a layout fails", async () => {
    layout.mockResolvedValueOnce(new Map([["a", { x: 1, y: 1 }]])).mockRejectedValueOnce(new Error("elk"));
    const { result, rerender } = renderHook(({ nodes }) => useGraphLayout(nodes, NO_EDGES), {
      initialProps: { nodes: FIRST },
    });
    await waitFor(() => expect(result.current.state).toBe("ready"));

    rerender({ nodes: SECOND });

    await waitFor(() => expect(result.current.state).toBe("error"));
    expect([...result.current.positions!.keys()]).toEqual(["a"]);
  });
});
