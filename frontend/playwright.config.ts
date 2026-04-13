import { defineConfig } from "@playwright/test";
import dotenv from "dotenv";

dotenv.config({ path: "./e2e/.env.e2e" });

export default defineConfig({
  testDir: "./e2e/tests",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 1,
  workers: 1,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
  },
  webServer: {
    command: "npm run dev",
    port: 5173,
    reuseExistingServer: true,
    timeout: 30_000,
  },
  projects: [
    {
      name: "auth-setup",
      testMatch: /auth\.setup\.ts/,
      testDir: "./e2e",
    },
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        storageState: ".auth/user.json",
      },
      dependencies: ["auth-setup"],
    },
  ],
});
