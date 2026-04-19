import { test, expect } from "@playwright/test";

test("endpoints: list loads and DEFAULT appears", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("[data-testid^='view-agent-']").first().click();
  await page.getByTestId("endpoints-tab").scrollIntoViewIfNeeded();

  await expect(page.getByTestId("endpoints-tab")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("endpoints-table")).toBeVisible({ timeout: 15_000 });

  // Every agent runtime ships with a DEFAULT endpoint — guard against an
  // empty table.
  await expect(page.getByTestId("endpoint-row-DEFAULT")).toBeVisible();
});

test.skip("endpoints: create staging, switch version, delete — destructive, manual only", async () => {
  // This flow mutates live AgentCore resources (creates + deletes endpoints
  // on a real sub-agent). Keep around for manual runs but skip in CI.
});
