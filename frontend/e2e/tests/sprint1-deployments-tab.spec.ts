import { test, expect } from "@playwright/test";

test("deployments tab lists versions", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/agents/);

  // Wait for at least one agent to load, then click the "Edit" icon button
  // in its row. AgentList uses an icon button with title=common.edit; there
  // is no anchor tag.
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  const editBtn = page.getByRole("button", { name: /^(Edit|编辑)$/ }).first();
  await editBtn.click();

  // Deployments section renders inside AgentEditForm; wait for its heading.
  await expect(page.getByTestId("deployments-tab")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("deployments-table")).toBeVisible({ timeout: 15_000 });

  // At least one version row should render — version numbers are monospace
  // cells starting with "v". This guards against rendering an empty table.
  const firstVersionCell = page.locator('[data-testid="deployments-table"] tbody tr td').first();
  await expect(firstVersionCell).toContainText(/^v\d+/);
});
