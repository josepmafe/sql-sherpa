import { defineConfig } from "@playwright/test";

// The tests run against the compose stack (`make up`, `make build-prod`); the port follows compose's SHERPA_UI_PORT.
const port = process.env.SHERPA_UI_PORT ?? "8090";

export default defineConfig({
  testDir: "e2e",
  // One browser at a time: the freshness test rebuilds the warehouse the other tests read.
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${port}`, browserName: "chromium" },
});
