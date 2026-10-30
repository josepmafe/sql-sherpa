import type { APIRequestContext, Page } from "@playwright/test";

interface CatalogResponse {
  domains: { tables: { name: string }[] }[];
}

// The marts the running app lists, so a spec does not keep its own copy of the data platform's list.
export async function martNames(request: APIRequestContext): Promise<string[]> {
  const response = await request.get("/api/catalog");
  const catalog = (await response.json()) as CatalogResponse;
  return catalog.domains.flatMap((domain) => domain.tables.map((table) => table.name));
}

// Aborts every request outside the app's origin (UI spec §8) and returns the URLs it aborted.
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
