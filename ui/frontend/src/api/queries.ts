import { keepPreviousData, useQuery } from "@tanstack/react-query";

import type { LineageParams } from "../screens/lineage/url";
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

// The graph for a selector. The node picked in the URL is not part of the key: picking one does not refetch. The
// previous graph stays while a new selector loads, so the controls do not flicker. `columns` also asks for each
// node's columns (the first such request after a build takes about 2 s on the server).
export function useLineage(params: Pick<LineageParams, "select" | "layers" | "failingOnly">, { columns }: { columns: boolean }) {
  const { select, layers, failingOnly } = params;
  return useQuery({
    queryKey: ["lineage", select, layers, failingOnly, columns],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/lineage", {
        params: { query: { select, layers: layers?.join(","), failing_only: failingOnly, columns } },
      });
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    placeholderData: keepPreviousData,
    ...QUERY_DEFAULTS,
  });
}

export function useLineageNode(uniqueId: string | undefined) {
  return useQuery({
    queryKey: ["lineage", "nodes", uniqueId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/lineage/nodes/{unique_id}", {
        params: { path: { unique_id: uniqueId ?? "" } },
      });
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    enabled: uniqueId !== undefined,
    ...QUERY_DEFAULTS,
  });
}

// A column's focus: every column it comes from and feeds, and the edges between them.
export function useColumnFocus(uniqueId: string | undefined, column: string | undefined) {
  return useQuery({
    queryKey: ["lineage", "columns", uniqueId, column],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/lineage/columns/{unique_id}/{column}", {
        params: { path: { unique_id: uniqueId ?? "", column: column ?? "" } },
      });
      if (error !== undefined) throw new ApiRequestError(apiError(error, response.status), response.status);
      return data;
    },
    enabled: uniqueId !== undefined && column !== undefined,
    ...QUERY_DEFAULTS,
  });
}
