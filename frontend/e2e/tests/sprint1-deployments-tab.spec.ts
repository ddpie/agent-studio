import { test, expect } from "@playwright/test";

test("deployments tab lists versions", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  // Wait for at least one agent to load, then click its "View" icon button
  // to reach the agent detail page where the deployments section lives.
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("[data-testid^='view-agent-']").first().click();

  // Deployments section renders inside AgentDetailPage; wait for its heading.
  await expect(page.getByTestId("deployments-tab")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("deployments-table")).toBeVisible({ timeout: 15_000 });

  // At least one version row should render — version numbers are monospace
  // cells starting with "v". This guards against rendering an empty table.
  const firstVersionCell = page.locator('[data-testid="deployments-table"] tbody tr td').first();
  await expect(firstVersionCell).toContainText(/^v\d+/);
});
