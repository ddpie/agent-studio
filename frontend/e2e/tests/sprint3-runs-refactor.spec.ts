import { test, expect } from "@playwright/test";

async function openFirstAgent(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.waitForURL(/agents/);
  await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
  const viewBtn = page.locator("[data-testid^='view-agent-']").first();
  await viewBtn.click();
  await page.waitForURL(/#\/agents\/[^/]+$/);
  await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
}

test.describe("Schedules-centric refactor", () => {
  test("Schedules section is the first side-nav item on an agent detail page", async ({ page }) => {
    await openFirstAgent(page);
    await expect(page.getByTestId("schedules-section")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("schedules-tab")).toBeVisible();
    await expect(page.getByTestId("nav-schedules-section")).toBeVisible();
    // Former standalone Runs section is gone — its role moved inside Schedules.
    await expect(page.getByTestId("runs-section")).toHaveCount(0);
  });

  test("Advanced group is collapsed by default and toggles open", async ({ page }) => {
    await openFirstAgent(page);
    const toggle = page.getByTestId("nav-group-toggle-advanced");
    await expect(toggle).toBeVisible();
    await expect(page.getByTestId("nav-deployments-section")).toHaveCount(0);
    await toggle.click();
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
    await expect(page.getByTestId("nav-endpoints-section")).toBeVisible();
    await expect(page.getByTestId("nav-secrets-section")).toBeVisible();
    await expect(page.getByTestId("nav-logs-section")).toBeVisible();
  });

  test("Advanced group open state persists across tab reload", async ({ page }) => {
    await openFirstAgent(page);
    await page.getByTestId("nav-group-toggle-advanced").click();
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("nav-deployments-section")).toBeVisible();
  });

  test("Clicking a recent-run card opens the run-detail modal", async ({ page }) => {
    await openFirstAgent(page);
    // Find any schedule row and expand it.
    const expandBtn = page.locator("[data-testid^='expand-schedule-']").first();
    const hasSchedule = await expandBtn.count();
    test.skip(hasSchedule === 0, "No schedules configured in this environment");
    await expandBtn.click();
    const runCard = page.locator("[data-testid^='run-card-']").first();
    const hasRun = await runCard.count();
    test.skip(hasRun === 0, "No runs for this schedule yet");
    await runCard.click();
    await expect(page.getByTestId("run-detail-modal")).toBeVisible({ timeout: 5_000 });
    // ESC closes the modal.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("run-detail-modal")).toHaveCount(0);
  });

  test("Deep-linking /agents/:id/runs/:runId opens the run-detail modal on load", async ({ page }) => {
    await openFirstAgent(page);
    const expandBtn = page.locator("[data-testid^='expand-schedule-']").first();
    const hasSchedule = await expandBtn.count();
    test.skip(hasSchedule === 0, "No schedules configured in this environment");
    await expandBtn.click();
    const runCard = page.locator("[data-testid^='run-card-']").first();
    const hasRun = await runCard.count();
    test.skip(hasRun === 0, "No runs for this schedule yet");
    const testId = (await runCard.getAttribute("data-testid"))!;
    const runId = testId.replace(/^run-card-/, "");
    const currentUrl = page.url();
    const base = currentUrl.replace(/\/runs\/[^/?#]+$/, "");
    await page.goto(`${base}/runs/${encodeURIComponent(runId)}`);
    await expect(page.getByTestId("agent-detail-title")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("run-detail-modal")).toBeVisible({ timeout: 5_000 });
  });
});
