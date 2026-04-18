import { test, expect } from "@playwright/test";

test("endpoints: create staging, switch version, delete", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await page.locator("a[href*='/edit/']").first().click();
  await expect(page.getByTestId("endpoints-table")).toBeVisible();

  await page.getByRole("button", { name: /create endpoint|创建端点/i }).click();
  await page.locator('input[placeholder*="staging"]').fill("staging");
  await page.getByRole("button", { name: /create/i }).click();
  await expect(page.getByTestId("endpoint-row-staging")).toBeVisible({ timeout: 30_000 });

  page.on("dialog", (d) => d.accept());
  await page.getByTestId("endpoint-row-staging").getByRole("button", { name: /delete/i }).click();
  await expect(page.getByTestId("endpoint-row-staging")).toBeHidden({ timeout: 10_000 });
});
