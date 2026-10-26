import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import type { Page } from "@playwright/test";

const REPO_ROOT = resolve(import.meta.dirname, "../../..");
const COMPOSE_FILE = resolve(REPO_ROOT, "docker-compose.yml");

// The admin login lives only inside the postgres container, so the check runs through `docker compose exec`.
export function adminScalar(sql: string): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-f",
      COMPOSE_FILE,
      "exec",
      "-T",
      "postgres",
      "sh",
      "-c",
      'psql -U "$SHERPA_ADMIN_USER" -d "$POSTGRES_DB" -tA -c "$0"',
      sql,
    ],
    { encoding: "utf8" },
  ).trim();
}

export function make(...args: string[]): string {
  return execFileSync("make", args, { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

export async function runQuery(page: Page, sql: string): Promise<void> {
  const editor = page.locator(".cm-content");
  await editor.click();
  await editor.fill(sql);
  await page.getByRole("button", { name: "Run", exact: true }).click();
}
