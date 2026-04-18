import { test, expect } from "@playwright/test";

test.describe("Sprint 1 — offline banner", () => {
  test("banner appears when offline, hides when back online", async ({ page, context }) => {
    await page.goto("/");
    await page.waitForURL(/agents/, { timeout: 30_000 });

    await context.setOffline(true);
    await expect(page.getByTestId("offline-banner")).toBeVisible();

    await context.setOffline(false);
    await expect(page.getByTestId("online-banner")).toBeVisible();
    await expect(page.getByTestId("online-banner")).toBeHidden({ timeout: 5000 });
  });
});
