import { test, expect } from "@playwright/test";

test("View button navigates to agent detail page", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });

  const viewBtn = page.locator("[data-testid^='view-agent-']").first();
  await expect(viewBtn).toBeVisible({ timeout: 15_000 });
  await viewBtn.click();

  await page.waitForURL(/#\/agents\/[^/]+$/);
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("deployments-tab")).toBeVisible();
});

test("Detail page linear sections all render", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("[data-testid^='view-agent-']").first().click();
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("deployments-tab")).toBeVisible();

  await page.getByTestId("endpoints-tab").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("endpoints-tab")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("evaluations-tab").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("evaluations-tab")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("traces-tab").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("traces-tab")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("integration-section").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("integration-tab")).toBeVisible();
});
