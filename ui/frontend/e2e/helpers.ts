import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { adminScalar } from "./warehouse";

interface CatalogResponse {
  domains: { tables: { name: string }[] }[];
}

// The marts the running app lists, checked against the warehouse's own marts schema so the app is not trusted
// with its own list. No spec keeps a copy of the data platform's list.
export async function martNames(request: APIRequestContext): Promise<string[]> {
  const response = await request.get("/api/catalog");
  expect(response.ok(), `GET /api/catalog answered ${response.status()}`).toBe(true);
  const catalog = (await response.json()) as CatalogResponse;
  const names = catalog.domains.flatMap((domain) => domain.tables.map((table) => table.name));
  const inWarehouse = adminScalar(
    "select string_agg(table_name, ',' order by table_name collate \"C\") from information_schema.tables where table_schema = 'marts'",
  );

  expect(names.length).toBeGreaterThan(0);
  // Both sides sort by code point: JS's default sort and the "C" collation.
  expect([...names].sort().join(",")).toBe(inWarehouse);
  return names;
}

// Aborts every request outside the app's origin (UI spec section 8) and returns the URLs it aborted.
export async function blockForeignRequests(page: Page, baseURL: string | undefined): Promise<string[]> {
  const origin = new URL(baseURL as string).origin;
  const blocked: string[] = [];
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (new URL(url).origin === origin) return route.continue();
    blocked.push(url);
    return route.abort();
  });
  return blocked;
}
