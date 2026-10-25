import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Same-origin: the API serves the built app, and the Vite dev server proxies /api.
export const api = createClient<paths>({ baseUrl: "" });
