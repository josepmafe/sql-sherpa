import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";

export type QueryResponse = components["schemas"]["QueryResponse"];
type SortSpec = components["schemas"]["SortSpec"];
type FilterSpec = components["schemas"]["FilterSpec"];

export type RunStatus = "idle" | "running" | "done" | "error" | "cancelled";
// The API's one error shape (UI spec §4); `position` is a 0-based code point offset into the SQL that was sent.
export type RunError = { code: string; message: string; position?: number };
export type RunOptions = { sort?: SortSpec[]; filters?: FilterSpec[] };
export type RunOutcome = {
  sql: string;
  status: "done" | "error" | "cancelled";
  returnedRows: number | null;
};

type RunState = {
  status: RunStatus;
  response?: QueryResponse;
  error?: RunError;
  elapsedSeconds: number;
};

const IDLE: RunState = { status: "idle", elapsedSeconds: 0 };
const TICK_MS = 1000;
const MS_PER_SECOND = 1000;
const NETWORK_ERROR: RunError = {
  code: "network_error",
  message: "Could not reach the server.",
};

type InFlight = {
  queryId: string;
  sql: string;
  controller: AbortController;
  startedAt: number;
};
type InFlightRef = { current: InFlight | undefined };

// One worksheet runs one query at a time. A new run, `cancel()` and unmounting all abandon the in-flight one:
// they clear `inFlight` first, and every response is applied only while its query_id is still the in-flight one,
// so a late answer for a superseded query can never overwrite a newer result.
export function useWorksheetRun(worksheetId: string, onSettled?: (outcome: RunOutcome) => void) {
  const [state, setState] = useState<RunState>(IDLE);
  const inFlight = useRef<InFlight | undefined>(undefined);
  const settled = useRef(onSettled);

  useEffect(() => {
    settled.current = onSettled;
  });

  // Leaving the worksheet (or the page) stops its query; results are never kept (UI spec §7).
  useEffect(
    () => () => {
      abandon(inFlight);
    },
    [worksheetId],
  );

  const running = state.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      const current = inFlight.current;
      if (current)
        setState((previous) => ({
          ...previous,
          elapsedSeconds: secondsSince(current.startedAt),
        }));
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [running]);

  const run = useCallback(async (sql: string, options: RunOptions = {}): Promise<void> => {
    abandon(inFlight);
    const current: InFlight = {
      queryId: crypto.randomUUID(),
      sql,
      controller: new AbortController(),
      startedAt: Date.now(),
    };
    inFlight.current = current;
    setState({ status: "running", elapsedSeconds: 0 });

    const finish = (next: RunState, returnedRows: number | null) => {
      if (inFlight.current?.queryId !== current.queryId) return; // superseded, cancelled or unmounted
      inFlight.current = undefined;
      setState(next);
      if (next.status === "done" || next.status === "error" || next.status === "cancelled") {
        settled.current?.({ sql, status: next.status, returnedRows });
      }
    };

    try {
      const { data, error, response } = await api.POST("/api/queries", {
        body: {
          query_id: current.queryId,
          sql,
          sort: options.sort ?? [],
          filters: options.filters ?? [],
        },
        signal: current.controller.signal,
      });
      const elapsedSeconds = secondsSince(current.startedAt);
      if (data) {
        finish({ status: "done", response: data, elapsedSeconds }, data.returned_rows);
        return;
      }
      const runError = toRunError(error, response.status);
      const status = runError.code === "cancelled" ? "cancelled" : "error";
      finish({ status, error: runError, elapsedSeconds }, null);
    } catch {
      // An abort lands here too, but then `finish` finds the query is no longer the in-flight one.
      finish(
        {
          status: "error",
          error: NETWORK_ERROR,
          elapsedSeconds: secondsSince(current.startedAt),
        },
        null,
      );
    }
  }, []);

  const cancel = useCallback((): void => {
    const current = inFlight.current;
    if (!current) return;
    abandon(inFlight);
    setState({
      status: "cancelled",
      elapsedSeconds: secondsSince(current.startedAt),
    });
    settled.current?.({
      sql: current.sql,
      status: "cancelled",
      returnedRows: null,
    });
  }, []);

  return { ...state, run, cancel };
}

function abandon(inFlight: InFlightRef): void {
  const current = inFlight.current;
  if (!current) return;
  inFlight.current = undefined;
  current.controller.abort();
  // The aborted request makes the server notice the disconnect, but only on its poll; this stops the query now.
  api
    .POST("/api/queries/{query_id}/cancel", {
      params: { path: { query_id: current.queryId } },
    })
    .catch(() => undefined);
}

function secondsSince(startedAt: number): number {
  return Math.floor((Date.now() - startedAt) / MS_PER_SECOND);
}

function toRunError(body: unknown, status: number): RunError {
  if (typeof body === "object" && body !== null && "code" in body && "message" in body) {
    const { code, message } = body;
    if (typeof code === "string" && typeof message === "string") {
      return { code, message, position: positionOf(body) };
    }
  }
  return {
    code: "http_error",
    message: `The request failed (HTTP ${status}).`,
  };
}

function positionOf(body: object): number | undefined {
  if (!("detail" in body) || typeof body.detail !== "object" || body.detail === null) return undefined;
  return "position" in body.detail && typeof body.detail.position === "number" ? body.detail.position : undefined;
}
