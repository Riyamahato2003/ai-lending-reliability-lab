import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
    ...devices["Desktop Chrome"],
    trace: "retain-on-failure",
  },
  reporter: [["list"], ["html", { open: "never" }]],
});
