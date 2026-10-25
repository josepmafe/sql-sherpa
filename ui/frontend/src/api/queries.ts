import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

// UI spec §2: data is never stale for long, and comes back when the window regains focus.
const STALE_TIME_MS = 30_000;
const QUERY_DEFAULTS = { staleTime: STALE_TIME_MS, refetchOnWindowFocus: true } as const;

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/health");
      if (error) throw new Error("health check failed");
      return data;
    },
    ...QUERY_DEFAULTS,
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/catalog");
      if (error) throw new Error("catalog request failed");
      return data;
    },
    ...QUERY_DEFAULTS,
  });
}

export function useTableDetails(name: string | undefined) {
  return useQuery({
    queryKey: ["catalog", "tables", name],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/catalog/tables/{name}", { params: { path: { name: name ?? "" } } });
      if (error) throw new Error(`table ${name} request failed`);
      return data;
    },
    enabled: name !== undefined,
    ...QUERY_DEFAULTS,
  });
}
