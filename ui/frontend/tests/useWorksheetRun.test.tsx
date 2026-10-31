import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWorksheetRun } from "../src/screens/explorer/useWorksheetRun";
import { apiFailure, noContent, ok, proxyFailure, queryResponse as buildQueryResponse } from "./fixtures/api";
import { type Deferred, deferred } from "./fixtures/render";

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));

type Call = {
  path: string;
  options: {
    body?: { query_id: string; sql: string };
    signal?: AbortSignal;
    params?: { path: { query_id: string } };
  };
};

function calls(path: string): Call[] {
  return post.mock.calls.filter(([p]) => p === path).map(([p, options]) => ({ path: p as string, options }));
}

function queryResponse(queryId: string, rows: number) {
  return ok(buildQueryResponse({ query_id: queryId, returned_rows: rows }));
}

describe("useWorksheetRun", () => {
  const pending: Deferred[] = [];

  beforeEach(() => {
    post.mockReset();
    pending.length = 0;
    post.mockImplementation((path: string) => {
      if (path === "/api/queries") {
        const d = deferred();
        pending.push(d);
        return d.promise;
      }
      return Promise.resolve(noContent());
    });
  });
  afterEach(() => vi.useRealTimers());

  it("ignores a superseded response", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    act(() => void result.current.run("select 2"));
    const [first, second] = calls("/api/queries").map((c) => c.options.body!.query_id) as [string, string];
    // The second answers first; the first (already cancelled) answers late and must not win.
    await act(async () => pending[1]!.resolve(queryResponse(second, 2)));
    await act(async () => pending[0]!.resolve(queryResponse(first, 1)));

    expect(result.current.status).toBe("done");
    expect(result.current.response?.query_id).toBe(second);
    expect(result.current.response?.returned_rows).toBe(2);
  });

  it("ignores a superseded failure", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    act(() => void result.current.run("select 2"));
    const second = calls("/api/queries")[1]!.options.body!.query_id;
    await act(async () => pending[1]!.resolve(queryResponse(second, 2)));
    await act(async () => pending[0]!.reject(new DOMException("aborted", "AbortError")));

    expect(result.current.status).toBe("done");
    expect(result.current.error).toBeUndefined();
  });

  it("a new run cancels the previous query_id", () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    act(() => void result.current.run("select 2"));

    const [first] = calls("/api/queries").map((c) => c.options.body!.query_id) as [string];
    expect(calls("/api/queries/{query_id}/cancel").map((c) => c.options.params!.path.query_id)).toEqual([first]);
    expect(calls("/api/queries")[0]!.options.signal!.aborted).toBe(true);
    expect(calls("/api/queries")[1]!.options.signal!.aborted).toBe(false);
    // The cancel is sent before the new query is.
    const order = post.mock.calls.map(([p]) => p);
    expect(order).toEqual(["/api/queries", "/api/queries/{query_id}/cancel", "/api/queries"]);
  });

  it("a run after a finished run sends no cancel", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    await act(async () => pending[0]!.resolve(queryResponse(calls("/api/queries")[0]!.options.body!.query_id, 1)));
    act(() => void result.current.run("select 2"));

    expect(calls("/api/queries")).toHaveLength(2);
    expect(calls("/api/queries/{query_id}/cancel")).toHaveLength(0);
  });

  it("cancel reports how long the query ran and drops its late response", async () => {
    vi.useFakeTimers();
    const settled = vi.fn();
    const { result } = renderHook(() => useWorksheetRun("w1", settled));

    act(() => void result.current.run("select pg_sleep(9)"));
    act(() => void vi.advanceTimersByTime(3200));
    expect(result.current.status).toBe("running");
    expect(result.current.elapsedSeconds).toBe(3);
    const queryId = calls("/api/queries")[0]!.options.body!.query_id;
    act(() => result.current.cancel());
    await act(async () => pending[0]!.resolve(queryResponse(queryId, 9)));

    expect(result.current.status).toBe("cancelled");
    expect(result.current.elapsedSeconds).toBe(3);
    expect(result.current.response).toBeUndefined();
    expect(settled).toHaveBeenCalledOnce();
    expect(settled).toHaveBeenCalledWith({
      sql: "select pg_sleep(9)",
      status: "cancelled",
      returnedRows: null,
    });
    expect(calls("/api/queries/{query_id}/cancel").map((c) => c.options.params!.path.query_id)).toEqual([queryId]);
  });

  it("reports API errors with their position and records the outcome", async () => {
    const settled = vi.fn();
    const { result } = renderHook(() => useWorksheetRun("w1", settled));

    act(() => void result.current.run("selec 1"));
    await act(async () =>
      pending[0]!.resolve(apiFailure("sql_error", 'syntax error at or near "selec"', 422, 0)),
    );

    expect(result.current.status).toBe("error");
    expect(result.current.error).toEqual({
      code: "sql_error",
      message: 'syntax error at or near "selec"',
      detail: { position: 0 },
    });
    expect(settled).toHaveBeenCalledWith({
      sql: "selec 1",
      status: "error",
      returnedRows: null,
    });
  });

  it("treats a server-side cancelled error as cancelled", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    await act(async () =>
      pending[0]!.resolve(apiFailure("cancelled", "Query cancelled.", 409)),
    );

    expect(result.current.status).toBe("cancelled");
  });

  it("shows a network failure and a non-API error body as errors", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"));

    act(() => void result.current.run("select 1"));
    await act(async () => pending[0]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.error?.code).toBe("network_error");

    act(() => void result.current.run("select 1"));
    await act(async () =>
      pending[1]!.resolve(proxyFailure("<html>bad gateway</html>", 502)),
    );
    expect(result.current.error).toEqual({
      code: "http_error",
      message: "The request failed (HTTP 502).",
    });
  });

  describe("a re-run with a sort or filter", () => {
    const SORT = { sort: [{ column: "n", direction: "asc" as const }], filters: [] };

    async function finishedRun(settled: () => void) {
      const hook = renderHook(() => useWorksheetRun("w1", settled));
      act(() => void hook.result.current.run("select 1"));
      await act(async () => pending[0]!.resolve(queryResponse(calls("/api/queries")[0]!.options.body!.query_id, 1)));
      return hook;
    }

    it("keeps the last result while it runs and when it fails, and is not reported", async () => {
      const settled = vi.fn();
      const { result } = await finishedRun(settled);
      settled.mockClear();
      const onApplied = vi.fn();

      act(() => void result.current.run("select 1", { ...SORT, onApplied }));
      expect(result.current.status).toBe("running");
      expect(result.current.response?.returned_rows).toBe(1);
      await act(async () =>
        pending[1]!.resolve(apiFailure("sql_error", "bad value", 422)),
      );

      expect(result.current.status).toBe("error");
      expect(result.current.error?.message).toBe("bad value");
      expect(result.current.response?.returned_rows).toBe(1);
      expect(onApplied).not.toHaveBeenCalled();
      expect(settled).not.toHaveBeenCalled();
      expect(calls("/api/queries")[1]!.options.body).toMatchObject({ sql: "select 1", sort: SORT.sort, filters: [] });
    });

    it("replaces the result with its answer and calls onApplied", async () => {
      const settled = vi.fn();
      const { result } = await finishedRun(settled);
      settled.mockClear();
      const onApplied = vi.fn();

      act(() => void result.current.run("select 1", { ...SORT, onApplied }));
      await act(async () => pending[1]!.resolve(queryResponse(calls("/api/queries")[1]!.options.body!.query_id, 7)));

      expect(result.current.status).toBe("done");
      expect(result.current.response?.returned_rows).toBe(7);
      expect(onApplied).toHaveBeenCalledOnce();
      expect(settled).not.toHaveBeenCalled();
    });

    it("keeps the last result when cancelled, and is not reported", async () => {
      const settled = vi.fn();
      const { result } = await finishedRun(settled);
      settled.mockClear();

      act(() => void result.current.run("select 1", { ...SORT, onApplied: () => {} }));
      act(() => result.current.cancel());

      expect(result.current.status).toBe("cancelled");
      expect(result.current.response?.returned_rows).toBe(1);
      expect(settled).not.toHaveBeenCalled();
    });

    it("a Run after it keeps the last result while it runs", async () => {
      const { result } = await finishedRun(() => {});

      act(() => void result.current.run("select 2"));

      expect(result.current.status).toBe("running");
      expect(result.current.response?.returned_rows).toBe(1);
    });

    it("a Run after it clears the last result when it fails", async () => {
      const { result } = await finishedRun(() => {});

      act(() => void result.current.run("select 2"));
      await act(async () =>
        pending[1]!.resolve(apiFailure("sql_error", "bad value", 422)),
      );

      expect(result.current.status).toBe("error");
      expect(result.current.response).toBeUndefined();
    });

    it("a Run after it clears the last result when it is cancelled", async () => {
      const { result } = await finishedRun(() => {});

      act(() => void result.current.run("select 2"));
      act(() => result.current.cancel());

      expect(result.current.status).toBe("cancelled");
      expect(result.current.response).toBeUndefined();
    });
  });

  it("aborts and cancels the running query on unmount, and ignores its answer", async () => {
    const settled = vi.fn();
    const { result, unmount } = renderHook(() => useWorksheetRun("w1", settled));

    act(() => void result.current.run("select 1"));
    const queryId = calls("/api/queries")[0]!.options.body!.query_id;
    unmount();
    await act(async () => pending[0]!.resolve(queryResponse(queryId, 1)));

    expect(calls("/api/queries")[0]!.options.signal!.aborted).toBe(true);
    expect(calls("/api/queries/{query_id}/cancel")).toHaveLength(1);
    expect(settled).not.toHaveBeenCalled();
  });

  it("survives StrictMode's double effects without cancelling anything", async () => {
    const { result } = renderHook(() => useWorksheetRun("w1"), {
      wrapper: StrictMode,
    });

    expect(calls("/api/queries/{query_id}/cancel")).toHaveLength(0);
    act(() => void result.current.run("select 1"));
    const queryId = calls("/api/queries")[0]!.options.body!.query_id;
    await act(async () => pending[0]!.resolve(queryResponse(queryId, 1)));

    expect(result.current.status).toBe("done");
    expect(calls("/api/queries/{query_id}/cancel")).toHaveLength(0);
  });
});
