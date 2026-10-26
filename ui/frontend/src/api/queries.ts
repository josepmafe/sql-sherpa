import { useQuery } from "@tanstack/react-query";

import { api, type ApiError, apiError } from "./client";

// UI spec §2: data is never stale for long, and comes back when the window regains focus.
const STALE_TIME_MS = 30_000;
// Health is also polled, so a database that goes down mid-session shows its banner without a focus change.
const HEALTH_POLL_MS = 30_000;
// Only a request that got no answer (the network) is retried, once: an API error answer would only repeat.
const NETWORK_RETRIES = 1;
const QUERY_DEFAULTS = {
  staleTime: STALE_TIME_MS,
  refetchOnWindowFocus: true,
  retry: (failureCount: number, error: Error) => !(error instanceof ApiRequestError) && failureCount < NETWORK_RETRIES,
} as const;

// An error answer from the API, with its code and message (UI spec §4).
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(error: ApiError, status: number) {
    super(error.message);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.status = status;
  }
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/health");
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    refetchInterval: HEALTH_POLL_MS,
    ...QUERY_DEFAULTS,
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/catalog");
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    ...QUERY_DEFAULTS,
  });
}

export function useTableDetails(name: string | undefined) {
  return useQuery({
    queryKey: ["catalog", "tables", name],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/catalog/tables/{name}", {
        params: { path: { name: name ?? "" } },
      });
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    enabled: name !== undefined,
    ...QUERY_DEFAULTS,
  });
}
