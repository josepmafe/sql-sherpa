import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type ApiError = components["schemas"]["ApiError"];

// Same-origin: the API serves the built app, and the Vite dev server proxies /api.
export const api = createClient<paths>({ baseUrl: "" });

// The API's one error shape (UI spec §4). A body that is not one (a proxy's HTML error page) becomes `http_error`.
export function apiError(body: unknown, status: number): ApiError {
  if (isRecord(body) && typeof body.code === "string" && typeof body.message === "string") {
    const position = isRecord(body.detail) && typeof body.detail.position === "number" ? body.detail.position : null;
    return { code: body.code, message: body.message, detail: { position } };
  }
  return { code: "http_error", message: `The request failed (HTTP ${status}).` };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
