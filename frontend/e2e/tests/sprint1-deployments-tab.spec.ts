import { test, expect } from "@playwright/test";

test("deployments tab lists versions", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await page.locator("a[href*='/edit/']").first().click();
  await expect(page.getByTestId("deployments-tab")).toBeVisible();
  await expect(page.getByTestId("deployments-table")).toBeVisible({ timeout: 10_000 });
});
