import { test, expect } from "@playwright/test";

test("traces tab renders session list or empty state", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^(Edit|编辑)$/ }).first().click();
  await expect(page.getByTestId("traces-tab")).toBeVisible({ timeout: 15_000 });

  const sessions = page.getByTestId("sessions-list");
  const empty = page.getByTestId("traces-tab").getByText(/No traces yet|暂无追踪记录/);
  await expect(sessions.or(empty)).toBeVisible();
});

test("clicking a session shows span tree", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^(Edit|编辑)$/ }).first().click();
  await expect(page.getByTestId("traces-tab")).toBeVisible({ timeout: 15_000 });

  const firstSession = page.locator("[data-testid^='session-row-']").first();
  if (await firstSession.isVisible().catch(() => false)) {
    await firstSession.click();
    await expect(page.getByTestId("span-tree")).toBeVisible({ timeout: 15_000 });
  } else {
    test.info().annotations.push({ type: "skip", description: "no trace sessions in this env" });
  }
});
