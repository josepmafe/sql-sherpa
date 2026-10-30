import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError, useCatalog, useHealth, useTableDetails } from "../src/api/queries";
import { apiFailure, health, ok, proxyFailure } from "./fixtures/api";
import { createTestQueryClient, queryClientWrapper } from "./fixtures/render";

const get = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { GET: (...args: unknown[]) => get(...args) },
}));

const wrapper = () => ({ wrapper: queryClientWrapper() });

describe("API hooks", () => {
  // A block body: a function returned from beforeEach would run as the teardown.
  beforeEach(() => {
    get.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("an API error is not retried and keeps its code and message", async () => {
    const message = "the dbt artifacts are missing; run make build-prod";
    get.mockResolvedValue(apiFailure("catalog_unavailable", message, 503));

    const { result } = renderHook(() => useCatalog(), wrapper());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledOnce();
    expect(result.current.error).toBeInstanceOf(ApiRequestError);
    expect(result.current.error).toMatchObject({ code: "catalog_unavailable", message, status: 503 });
  });

  it("an error body that is not the API's still becomes an error with the HTTP status", async () => {
    get.mockResolvedValue(proxyFailure("<html>bad gateway</html>", 502));

    const { result } = renderHook(() => useCatalog(), wrapper());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledOnce();
    expect(result.current.error).toMatchObject({ code: "http_error", message: "The request failed (HTTP 502)." });
  });

  it("a network failure is retried once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // fetch() rejects with a TypeError when no answer comes.
    get.mockImplementation(() => {
      throw new TypeError("Failed to fetch");
    });

    const { result } = renderHook(() => useHealth(), wrapper());

    await waitFor(() => expect(get).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(1_000); // the retry delay
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("health is checked again every 30 seconds, so a database that goes down shows its banner", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    get.mockResolvedValueOnce(ok(health())).mockResolvedValue(ok(health({ database: "down" })));
    const { result } = renderHook(() => useHealth(), wrapper());
    await waitFor(() => expect(result.current.data?.database).toBe("ok"));

    await vi.advanceTimersByTimeAsync(29_000);
    expect(get).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1_000);

    await waitFor(() => expect(result.current.data?.database).toBe("down"));
    expect(get).toHaveBeenCalledTimes(2);
  });

  // UI spec §2: data is never stale for long and comes back when the window regains focus. The options sit on the
  // observer the hook registers.
  it.each<[string, () => { isSuccess: boolean }, string[]]>([
    ["health", () => useHealth(), ["health"]],
    ["the catalog", () => useCatalog(), ["catalog"]],
    ["a table", () => useTableDetails("fct_orders"), ["catalog", "tables", "fct_orders"]],
  ])("%s is stale after at most 30 seconds and refetches on window focus", async (_name, hook, key) => {
    get.mockResolvedValue(ok(health()));
    const client = createTestQueryClient();

    const { result } = renderHook(hook, { wrapper: queryClientWrapper(client) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const options = client.getQueryCache().find({ queryKey: key })?.observers[0]?.options;
    expect(options?.staleTime).toBeLessThanOrEqual(30_000);
    expect(options?.refetchOnWindowFocus).toBe(true);
  });
});
