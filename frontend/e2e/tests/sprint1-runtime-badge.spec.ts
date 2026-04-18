import { test, expect } from "@playwright/test";

test("runtime badge appears and opens drawer", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  const badge = page.getByTestId("status-badge").first();
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await badge.click();
  await expect(page.getByTestId("runtime-drawer")).toBeVisible();
  await page.getByRole("button", { name: /close|关闭/i }).first().click();
  await expect(page.getByTestId("runtime-drawer")).toBeHidden();
});
