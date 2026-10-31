/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `make ui-dev` runs the API on SHERPA_UI_PORT (default 8090); the dev server forwards /api to it.
const apiPort = process.env.SHERPA_UI_PORT ?? "8090";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": `http://127.0.0.1:${apiPort}` } },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.ts"],
    testTimeout: 20_000, // a loaded machine (the backend suite running beside) slows the first render of a screen
    restoreMocks: true, // a vi.spyOn on a global does not leak into the next test
    unstubGlobals: true, // so does a vi.stubGlobal
  },
});
