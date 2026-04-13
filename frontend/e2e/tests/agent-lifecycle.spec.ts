import { test, expect } from "../fixtures/test";

test.describe("Agent Lifecycle", () => {
  test.setTimeout(180_000);

  test("archive agent and verify in archived section", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Find agents in the list (not Meta-Agent)
    const agentCards = page.locator(".group.w-full").filter({ has: page.locator("svg.lucide-archive") });
    const agentCount = await agentCards.count();

    if (agentCount === 0) {
      test.skip(true, "No agents available to test archive");
      return;
    }

    const firstAgent = agentCards.first();

    // Click archive button
    const archiveBtn = firstAgent.locator("button").filter({ has: page.locator("svg.lucide-archive") });
    await archiveBtn.click();

    // Confirm dialog should appear
    const confirmDialog = page.locator(".fixed.inset-0").last();
    await expect(confirmDialog).toBeVisible({ timeout: 5_000 });

    // Click confirm (Archive button in dialog)
    await confirmDialog.getByRole("button", { name: /archive|归档/i }).click();

    // Wait for Meta-Agent streaming to complete the archive operation
    await page.waitForTimeout(15_000);

    // The archived section should appear or be expandable
    const archivedToggle = page.getByText(/archived|已归档/i);
    if (await archivedToggle.isVisible()) {
      await archivedToggle.click();
      await page.waitForTimeout(1_000);

      // The archived agent should appear in the archived section
      const archivedSection = page.locator(".border-dashed");
      const archivedCount = await archivedSection.count();
      expect(archivedCount).toBeGreaterThan(0);

      // Restore it back
      const restoreBtn = archivedSection.first().locator("button").filter({ has: page.locator("svg.lucide-rotate-ccw") });
      await restoreBtn.click();

      // Confirm restore
      const restoreDialog = page.locator(".fixed.inset-0").last();
      if (await restoreDialog.isVisible()) {
        await restoreDialog.getByRole("button", { name: /restore|恢复/i }).click();
        await page.waitForTimeout(15_000);
      }
    }
  });

  test("refresh agent list button works", async ({ page }) => {
    await page.goto("/#/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // Find refresh button
    const refreshBtn = page.locator("button").filter({ has: page.locator("svg.lucide-refresh-cw") }).first();
    await expect(refreshBtn).toBeVisible();

    // Click refresh
    await refreshBtn.click();

    // Should show loading spinner briefly
    await page.waitForTimeout(2_000);

    // Refresh button should be back (not spinning)
    await expect(refreshBtn).toBeEnabled({ timeout: 10_000 });
  });
});
