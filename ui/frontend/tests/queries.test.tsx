import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError, useCatalog, useHealth } from "../src/api/queries";

const get = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { GET: (...args: unknown[]) => get(...args) },
}));

const HEALTHY = { data: { database: "ok", artifacts: { status: "ok", generated_at: null } }, response: { status: 200 } };

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("API hooks", () => {
  // A block body: a function returned from beforeEach would run as the teardown.
  beforeEach(() => {
    get.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("an API error is not retried and keeps its code and message", async () => {
    const message = "the dbt artifacts are missing; run make build-prod";
    get.mockResolvedValue({ error: { code: "catalog_unavailable", message }, response: { status: 503 } });

    const { result } = renderHook(() => useCatalog(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledOnce();
    expect(result.current.error).toBeInstanceOf(ApiRequestError);
    expect(result.current.error).toMatchObject({ code: "catalog_unavailable", message, status: 503 });
  });

  it("an error body that is not the API's still becomes an error with the HTTP status", async () => {
    get.mockResolvedValue({ error: "<html>bad gateway</html>", response: { status: 502 } });

    const { result } = renderHook(() => useCatalog(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledOnce();
    expect(result.current.error).toMatchObject({ code: "http_error", message: "The request failed (HTTP 502)." });
  });

  it("a network failure is retried once", async () => {
    // fetch() rejects with a TypeError when no answer comes.
    get.mockImplementation(() => {
      throw new TypeError("Failed to fetch");
    });

    const { result } = renderHook(() => useHealth(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("health is checked again every 30 seconds, so a database that goes down shows its banner", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    get.mockResolvedValueOnce(HEALTHY).mockResolvedValue({ ...HEALTHY, data: { ...HEALTHY.data, database: "down" } });
    const { result } = renderHook(() => useHealth(), { wrapper });
    await waitFor(() => expect(result.current.data?.database).toBe("ok"));

    await vi.advanceTimersByTimeAsync(29_000);
    expect(get).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1_000);

    await waitFor(() => expect(result.current.data?.database).toBe("down"));
    expect(get).toHaveBeenCalledTimes(2);
  });
});
